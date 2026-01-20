"""
模块名称：视频切片组件

本模块使用 FFmpeg 将单个视频切分为固定时长片段，并输出带元数据的 `Data` 列表。
主要功能包括：
- 校验视频路径与时长
- 生成切片文件并回填时间戳元数据
- 可选保留原视频作为额外输出

关键组件：
- `SplitVideoComponent`
- `get_video_duration`
- `process_video`

设计背景：为 TwelveLabs 视频索引与问答提供可控粒度的视频片段。
注意事项：Astra 云环境不允许本地文件访问；路径会做安全字符校验。
"""

import hashlib
import math
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lfx.custom import Component
from lfx.inputs import BoolInput, DropdownInput, HandleInput, IntInput
from lfx.schema import Data
from lfx.template import Output
from lfx.utils.validate_cloud import raise_error_if_astra_cloud_disable_component

disable_component_in_astra_cloud_msg = (
    "Video processing is not supported in Astra cloud environment. "
    "Video components require local file system access for processing. "
    "Please use local storage mode or process videos locally before uploading."
)


class SplitVideoComponent(Component):
    """视频切片组件。

    契约：
    - 输入：单个视频路径与切片时长
    - 输出：`list[Data]`，每项包含片段文件路径与元数据
    - 副作用：在本地文件系统生成切片文件与目录
    - 失败语义：ffprobe/ffmpeg 错误或路径非法时抛异常
    """

    display_name = "Split Video"
    description = "Split a video into multiple clips of specified duration."
    icon = "TwelveLabs"
    name = "SplitVideo"
    documentation = "https://github.com/twelvelabs-io/twelvelabs-developer-experience/blob/main/integrations/Langflow/TWELVE_LABS_COMPONENTS_README.md"

    inputs = [
        HandleInput(
            name="videodata",
            display_name="Video Data",
            info="Input video data from VideoFile component",
            required=True,
            input_types=["Data"],
        ),
        IntInput(
            name="clip_duration",
            display_name="Clip Duration (seconds)",
            info="Duration of each clip in seconds",
            required=True,
            value=30,
        ),
        DropdownInput(
            name="last_clip_handling",
            display_name="Last Clip Handling",
            info=(
                "How to handle the final clip when it would be shorter than the specified duration:\n"
                "- Truncate: Skip the final clip entirely if it's shorter than the specified duration\n"
                "- Overlap Previous: Start the final clip earlier to maintain full duration, "
                "overlapping with previous clip\n"
                "- Keep Short: Keep the final clip at its natural length, even if shorter than specified duration"
            ),
            options=["Truncate", "Overlap Previous", "Keep Short"],
            value="Overlap Previous",
            required=True,
        ),
        BoolInput(
            name="include_original",
            display_name="Include Original Video",
            info="Whether to include the original video in the output",
            value=False,
        ),
    ]

    outputs = [
        Output(
            name="clips",
            display_name="Video Clips",
            method="process",
            output_types=["Data"],
        ),
    ]

    def get_video_duration(self, video_path: str) -> float:
        """使用 ffprobe 获取视频时长（秒）。"""
        try:
            # 注意：校验路径字符，规避注入风险
            if not isinstance(video_path, str) or any(c in video_path for c in ";&|`$(){}[]<>*?!#~"):
                error_msg = "Invalid video path"
                raise ValueError(error_msg)

            cmd = [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                video_path,
            ]
            result = subprocess.run(  # noqa: S603
                cmd,
                capture_output=True,
                text=True,
                check=False,
                shell=False,  # 注意：明确禁用 shell
            )
            if result.returncode != 0:
                error_msg = f"FFprobe error: {result.stderr}"
                raise RuntimeError(error_msg)
            return float(result.stdout.strip())
        except Exception as e:
            self.log(f"Error getting video duration: {e!s}", "ERROR")
            raise

    def get_output_dir(self, video_path: str) -> str:
        """生成唯一输出目录（基于文件名+时间戳+哈希）。"""
        # 注意：以原文件名作为目录前缀，便于排障定位
        path_obj = Path(video_path)
        base_name = path_obj.stem

        # 注意：使用 UTC 时间戳，避免本地时区差异
        timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")

        # 注意：用路径哈希避免同名文件冲突
        path_hash = hashlib.sha256(video_path.encode()).hexdigest()[:8]

        # 生成输出目录路径
        output_dir = Path(path_obj.parent) / f"clips_{base_name}_{timestamp}_{path_hash}"

        # 若目录不存在则创建
        output_dir.mkdir(parents=True, exist_ok=True)

        return str(output_dir)

    def process_video(self, video_path: str, clip_duration: int, *, include_original: bool) -> list[Data]:
        """执行视频切片并返回 `Data` 列表。

        契约：
        - 输入：`video_path` 与 `clip_duration`
        - 输出：切片 `Data` 列表（可包含原视频）
        - 副作用：生成切片文件与目录
        - 失败语义：ffprobe/ffmpeg 失败会抛异常

        关键路径（三步）：
        1) 读取视频时长并计算切片数
        2) 逐段调用 ffmpeg 输出切片文件
        3) 组装元数据并返回 `Data`

        异常流：ffprobe/ffmpeg 失败会抛 `RuntimeError` 并写日志。
        """
        try:
            # 获取视频时长
            total_duration = self.get_video_duration(video_path)

            # 计算切片数量（向上取整，覆盖末尾短片段）
            num_clips = math.ceil(total_duration / clip_duration)
            self.log(
                f"Total duration: {total_duration}s, Clip duration: {clip_duration}s, Number of clips: {num_clips}"
            )

            # 创建输出目录
            output_dir = self.get_output_dir(video_path)

            # 读取原视频信息
            path_obj = Path(video_path)
            original_filename = path_obj.name
            original_name = path_obj.stem

            # 结果列表（可包含原视频）
            video_paths: list[Data] = []

            # 可选：保留原视频
            if include_original:
                original_data: dict[str, Any] = {
                    "text": video_path,
                    "metadata": {
                        "source": video_path,
                        "type": "video",
                        "clip_index": -1,  # 注意：-1 表示原视频
                        "duration": int(total_duration),
                        "original_video": {
                            "name": original_name,
                            "filename": original_filename,
                            "path": video_path,
                            "duration": int(total_duration),
                            "total_clips": int(num_clips),
                            "clip_duration": int(clip_duration),
                        },
                    },
                }
                video_paths.append(Data(data=original_data))

            # 切分视频
            for i in range(int(num_clips)):
                start_time = float(i * clip_duration)
                end_time = min(float((i + 1) * clip_duration), total_duration)
                duration = end_time - start_time

                # 最后一个片段不足时长时的策略分支
                if i == int(num_clips) - 1 and duration < clip_duration:
                    if self.last_clip_handling == "Truncate":
                        # 丢弃过短片段
                        continue
                    if self.last_clip_handling == "Overlap Previous" and i > 0:
                        # 向前回退以补齐时长
                        start_time = total_duration - clip_duration
                        duration = clip_duration
                    # 保持短片策略：保持原始时长

                # 跳过不足 1 秒的片段
                if duration < 1:
                    continue

                # 生成输出路径
                output_path = Path(output_dir) / f"clip_{i:03d}.mp4"
                output_path_str = str(output_path)

                try:
                    # 调用 ffmpeg 生成片段
                    cmd = [
                        "ffmpeg",
                        "-i",
                        video_path,
                        "-ss",
                        str(start_time),
                        "-t",
                        str(duration),
                        "-c:v",
                        "libx264",
                        "-c:a",
                        "aac",
                        "-y",  # 覆盖已有文件
                        output_path_str,
                    ]

                    result = subprocess.run(  # noqa: S603
                        cmd,
                        capture_output=True,
                        text=True,
                        check=False,
                        shell=False,  # 注意：明确禁用 shell
                    )
                    if result.returncode != 0:
                        error_msg = f"FFmpeg error: {result.stderr}"
                        raise RuntimeError(error_msg)

                    # 生成时间戳字符串
                    start_min = int(start_time // 60)
                    start_sec = int(start_time % 60)
                    end_min = int(end_time // 60)
                    end_sec = int(end_time % 60)
                    timestamp_str = f"{start_min:02d}:{start_sec:02d} - {end_min:02d}:{end_sec:02d}"

                    # 组装片段元数据
                    clip_data: dict[str, Any] = {
                        "text": output_path_str,
                        "metadata": {
                            "source": video_path,
                            "type": "video",
                            "clip_index": i,
                            "start_time": float(start_time),
                            "end_time": float(end_time),
                            "duration": float(duration),
                            "original_video": {
                                "name": original_name,
                                "filename": original_filename,
                                "path": video_path,
                                "duration": int(total_duration),
                                "total_clips": int(num_clips),
                                "clip_duration": int(clip_duration),
                            },
                            "clip": {
                                "index": i,
                                "total": int(num_clips),
                                "duration": float(duration),
                                "start_time": float(start_time),
                                "end_time": float(end_time),
                                "timestamp": timestamp_str,
                            },
                        },
                    }
                    video_paths.append(Data(data=clip_data))

                except Exception as e:
                    self.log(f"Error processing clip {i}: {e!s}", "ERROR")
                    raise

            self.log(f"Created {len(video_paths)} clips in {output_dir}")
        except Exception as e:
            self.log(f"Error processing video: {e!s}", "ERROR")
            raise
        else:
            return video_paths

    def process(self) -> list[Data]:
        """执行切片流程并返回 `Data` 列表。

        契约：
        - 输入：`videodata`（单个视频）
        - 输出：切片 `Data` 列表
        - 副作用：访问本地文件系统
        - 失败语义：输入不合法或路径不可用时抛异常
        """
        # 注意：Astra 云环境禁止本地文件访问
        raise_error_if_astra_cloud_disable_component(disable_component_in_astra_cloud_msg)

        try:
            # 校验输入仅包含一个视频
            if not hasattr(self, "videodata") or not isinstance(self.videodata, list) or len(self.videodata) != 1:
                error_msg = "Please provide exactly one video"
                raise ValueError(error_msg)

            video_path = self.videodata[0].data.get("text")
            if not video_path or not Path(video_path).exists():
                error_msg = "Invalid video path"
                raise ValueError(error_msg)

            # 校验路径字符，规避注入风险
            if not isinstance(video_path, str) or any(c in video_path for c in ";&|`$(){}[]<>*?!#~"):
                error_msg = "Invalid video path contains unsafe characters"
                raise ValueError(error_msg)

            # 执行切片
            return self.process_video(video_path, self.clip_duration, include_original=self.include_original)

        except Exception as e:
            self.log(f"Error in split video component: {e!s}", "ERROR")
            raise
