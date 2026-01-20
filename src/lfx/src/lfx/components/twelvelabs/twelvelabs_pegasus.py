"""
模块名称：TwelveLabs Pegasus 视频问答

本模块提供 TwelveLabs Pegasus 的视频索引与问答能力，支持已有视频 ID 的直接问答。
主要功能包括：
- 获取或创建索引并上传视频
- 轮询任务状态并生成问答结果
- 校验视频文件并暴露视频 ID

关键组件：
- `TwelveLabsPegasus`
- `_get_or_create_index`
- `process_video`

设计背景：将视频索引与问答能力封装为 Langflow 组件。
注意事项：FFprobe 校验与任务轮询可能产生较长等待时间。
"""

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from tenacity import retry, stop_after_attempt, wait_exponential
from twelvelabs import TwelveLabs

from lfx.custom import Component
from lfx.field_typing.range_spec import RangeSpec
from lfx.inputs import DataInput, DropdownInput, MessageInput, MultilineInput, SecretStrInput, SliderInput
from lfx.io import Output
from lfx.schema.message import Message


class TaskError(Exception):
    """任务执行失败。"""


class TaskTimeoutError(Exception):
    """任务等待超时。"""


class IndexCreationError(Exception):
    """索引创建或解析失败。"""


class ApiRequestError(Exception):
    """API 请求失败。"""


class VideoValidationError(Exception):
    """视频校验失败。"""


class TwelveLabsPegasus(Component):
    """TwelveLabs Pegasus 视频问答组件。

    契约：
    - 输入：API Key、视频路径/视频 ID、索引信息与提问文本
    - 输出：`Message`（回答文本或错误提示）
    - 副作用：上传视频、轮询任务、调用生成接口
    - 失败语义：索引/任务/API 异常返回错误消息并清空缓存 ID
    """

    display_name = "TwelveLabs Pegasus"
    description = "Chat with videos using TwelveLabs Pegasus API."
    icon = "TwelveLabs"
    name = "TwelveLabsPegasus"
    documentation = "https://github.com/twelvelabs-io/twelvelabs-developer-experience/blob/main/integrations/Langflow/TWELVE_LABS_COMPONENTS_README.md"

    inputs = [
        DataInput(name="videodata", display_name="Video Data", info="Video Data", is_list=True),
        SecretStrInput(
            name="api_key", display_name="TwelveLabs API Key", info="Enter your TwelveLabs API Key.", required=True
        ),
        MessageInput(
            name="video_id",
            display_name="Pegasus Video ID",
            info="Enter a Video ID for a previously indexed video.",
        ),
        MessageInput(
            name="index_name",
            display_name="Index Name",
            info="Name of the index to use. If the index doesn't exist, it will be created.",
            required=False,
        ),
        MessageInput(
            name="index_id",
            display_name="Index ID",
            info="ID of an existing index to use. If provided, index_name will be ignored.",
            required=False,
        ),
        DropdownInput(
            name="model_name",
            display_name="Model",
            info="Pegasus model to use for indexing",
            options=["pegasus1.2"],
            value="pegasus1.2",
            advanced=False,
        ),
        MultilineInput(
            name="message",
            display_name="Prompt",
            info="Message to chat with the video.",
            required=True,
        ),
        SliderInput(
            name="temperature",
            display_name="Temperature",
            value=0.7,
            range_spec=RangeSpec(min=0, max=1, step=0.01),
            info=(
                "Controls randomness in responses. Lower values are more deterministic, "
                "higher values are more creative."
            ),
        ),
    ]

    outputs = [
        Output(
            display_name="Message",
            name="response",
            method="process_video",
            type_=Message,
        ),
        Output(
            display_name="Video ID",
            name="processed_video_id",
            method="get_video_id",
            type_=Message,
        ),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)

        self._task_id: str | None = None
        self._video_id: str | None = None
        self._index_id: str | None = None
        self._index_name: str | None = None
        self._message: str | None = None

    def _get_or_create_index(self, client: TwelveLabs) -> tuple[str, str]:
        """获取或创建索引，返回 (index_id, index_name)。"""
        # 注意：优先使用传入的 index_id
        if hasattr(self, "_index_id") and self._index_id:
            try:
                index = client.index.retrieve(id=self._index_id)
                self.log(f"Found existing index with ID: {self._index_id}")
            except (ValueError, KeyError) as e:
                self.log(f"Error retrieving index with ID {self._index_id}: {e!s}", "WARNING")
            else:
                return self._index_id, index.name

        # 注意：按名称查找，未命中则创建
        if hasattr(self, "_index_name") and self._index_name:
            try:
                # 拉取索引列表并按名称匹配
                indexes = client.index.list()
                for idx in indexes:
                    if idx.name == self._index_name:
                        self.log(f"Found existing index: {self._index_name} (ID: {idx.id})")
                        return idx.id, idx.name

                # 未命中则创建索引
                self.log(f"Creating new index: {self._index_name}")
                index = client.index.create(
                    name=self._index_name,
                    models=[
                        {
                            "name": self.model_name if hasattr(self, "model_name") else "pegasus1.2",
                            "options": ["visual", "audio"],
                        }
                    ],
                )
            except (ValueError, KeyError) as e:
                self.log(f"Error with index name {self._index_name}: {e!s}", "ERROR")
                error_message = f"Error with index name {self._index_name}"
                raise IndexCreationError(error_message) from e
            else:
                return index.id, index.name

        # 注意：两者都未提供时创建临时索引
        try:
            index_name = f"index_{int(time.time())}"
            self.log(f"Creating new index: {index_name}")
            index = client.index.create(
                name=index_name,
                models=[
                    {
                        "name": self.model_name if hasattr(self, "model_name") else "pegasus1.2",
                        "options": ["visual", "audio"],
                    }
                ],
            )
        except (ValueError, KeyError) as e:
            self.log(f"Failed to create new index: {e!s}", "ERROR")
            error_message = "Failed to create new index"
            raise IndexCreationError(error_message) from e
        else:
            return index.id, index.name

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10), reraise=True)
    async def _make_api_request(self, method: Any, *args: Any, **kwargs: Any) -> Any:
        """执行 API 请求并带指数退避重试。"""
        try:
            return await method(*args, **kwargs)
        except (ValueError, KeyError) as e:
            self.log(f"API request failed: {e!s}", "ERROR")
            error_message = "API request failed"
            raise ApiRequestError(error_message) from e

    def wait_for_task_completion(
        self, client: TwelveLabs, task_id: str, max_retries: int = 120, sleep_time: int = 5
    ) -> Any:
        """等待任务完成并包含超时与错误阈值控制。

        关键路径（三步）：
        1) 轮询任务状态并刷新日志/状态
        2) 根据 `ready/failed/error` 做分支
        3) 达到超时或连续错误阈值时抛异常
        """
        retries = 0
        consecutive_errors = 0
        max_consecutive_errors = 3

        while retries < max_retries:
            try:
                self.log(f"Checking task status (attempt {retries + 1})")
                result = client.task.retrieve(id=task_id)
                consecutive_errors = 0  # 注意：成功后清零连续错误计数

                if result.status == "ready":
                    self.log("Task completed successfully!")
                    return result
                if result.status == "failed":
                    error_msg = f"Task failed with status: {result.status}"
                    self.log(error_msg, "ERROR")
                    raise TaskError(error_msg)
                if result.status == "error":
                    error_msg = f"Task encountered an error: {getattr(result, 'error', 'Unknown error')}"
                    self.log(error_msg, "ERROR")
                    raise TaskError(error_msg)

                time.sleep(sleep_time)
                retries += 1
                status_msg = f"Processing video... {retries * sleep_time}s elapsed"
                self.status = status_msg
                self.log(status_msg)

            except (ValueError, KeyError) as e:
                consecutive_errors += 1
                error_msg = f"Error checking task status: {e!s}"
                self.log(error_msg, "WARNING")

                if consecutive_errors >= max_consecutive_errors:
                    too_many_errors = "Too many consecutive errors"
                    raise TaskError(too_many_errors) from e

                time.sleep(sleep_time * 2)
                continue

        timeout_msg = f"Timeout after {max_retries * sleep_time} seconds"
        self.log(timeout_msg, "ERROR")
        raise TaskTimeoutError(timeout_msg)

    def validate_video_file(self, filepath: str) -> tuple[bool, str]:
        """使用 ffprobe 校验视频文件，返回 (is_valid, error_message)。"""
        # 注意：校验路径字符以规避注入风险
        if not isinstance(filepath, str) or any(c in filepath for c in ";&|`$(){}[]<>*?!#~"):
            return False, "Invalid filepath"

        try:
            cmd = [
                "ffprobe",
                "-loglevel",
                "error",
                "-show_entries",
                "stream=codec_type,codec_name",
                "-of",
                "default=nw=1",
                "-print_format",
                "json",
                "-show_format",
                filepath,
            ]

            # 注意：使用参数列表并禁用 shell
            result = subprocess.run(  # noqa: S603
                cmd,
                capture_output=True,
                text=True,
                check=False,
                shell=False,  # 注意：明确禁用 shell
            )

            if result.returncode != 0:
                return False, f"FFprobe error: {result.stderr}"

            probe_data = json.loads(result.stdout)

            has_video = any(stream.get("codec_type") == "video" for stream in probe_data.get("streams", []))

            if not has_video:
                return False, "No video stream found in file"

            self.log(f"Video validation successful: {json.dumps(probe_data, indent=2)}")
        except subprocess.SubprocessError as e:
            return False, f"FFprobe process error: {e!s}"
        except json.JSONDecodeError as e:
            return False, f"FFprobe output parsing error: {e!s}"
        except (ValueError, OSError) as e:
            return False, f"Validation error: {e!s}"
        else:
            return True, ""

    def on_task_update(self, task: Any) -> None:
        """任务状态更新回调，刷新组件状态文本。"""
        self.status = f"Processing video... Status: {task.status}"
        self.log(self.status)

    def process_video(self) -> Message:
        """执行视频索引与问答流程。

        契约：
        - 输入：API Key、视频路径或 video_id、提问文本
        - 输出：`Message`（回答或错误提示）
        - 副作用：上传视频、轮询任务、调用生成接口
        - 失败语义：索引/任务/API 异常返回错误消息

        关键路径（三步）：
        1) 解析输入，优先使用已有 `video_id`
        2) 需要时上传视频并等待任务完成
        3) 生成回答或返回视频 ID 提示

        异常流：索引/任务/API 异常会返回错误消息并清空缓存 ID。
        """
        # 解析输入并同步到内部缓存
        if hasattr(self, "index_id") and self.index_id:
            self._index_id = self.index_id.text if hasattr(self.index_id, "text") else self.index_id

        if hasattr(self, "index_name") and self.index_name:
            self._index_name = self.index_name.text if hasattr(self.index_name, "text") else self.index_name

        if hasattr(self, "video_id") and self.video_id:
            self._video_id = self.video_id.text if hasattr(self.video_id, "text") else self.video_id

        if hasattr(self, "message") and self.message:
            self._message = self.message.text if hasattr(self.message, "text") else self.message

        try:
            # 已有 video_id 且包含提问时，直接生成回答
            if self._message and self._video_id and self._video_id != "":
                self.status = f"Have video id: {self._video_id}"

                client = TwelveLabs(api_key=self.api_key)

                self.status = f"Processing query (w/ video ID): {self._video_id} {self._message}"
                self.log(self.status)

                response = client.generate.text(
                    video_id=self._video_id,
                    prompt=self._message,
                    temperature=self.temperature,
                )
                return Message(text=response.data)

            # 否则走新视频索引流程
            if not self.videodata or not isinstance(self.videodata, list) or len(self.videodata) != 1:
                return Message(text="Please provide exactly one video")

            video_path = self.videodata[0].data.get("text")
            if not video_path or not Path(video_path).exists():
                return Message(text="Invalid video path")

            if not self.api_key:
                return Message(text="No API key provided")

            client = TwelveLabs(api_key=self.api_key)

            # 获取或创建索引
            try:
                index_id, index_name = self._get_or_create_index(client)
                self.status = f"Using index: {index_name} (ID: {index_id})"
                self.log(f"Using index: {index_name} (ID: {index_id})")
                self._index_id = index_id
                self._index_name = index_name
            except IndexCreationError as e:
                return Message(text=f"Failed to get/create index: {e}")

            with Path(video_path).open("rb") as video_file:
                task = client.task.create(index_id=self._index_id, file=video_file)
            self._task_id = task.id

            # 等待索引任务完成
            task.wait_for_done(sleep_interval=5, callback=self.on_task_update)

            if task.status != "ready":
                return Message(text=f"Processing failed with status {task.status}")

            # 缓存 video_id 供后续问答使用
            self._video_id = task.video_id

            # 若包含提问则生成回答
            if self._message:
                self.status = f"Processing query: {self._message}"
                self.log(self.status)

                response = client.generate.text(
                    video_id=self._video_id,
                    prompt=self._message,
                    temperature=self.temperature,
                )
                return Message(text=response.data)

            success_msg = (
                f"Video processed successfully. You can now ask questions about the video. Video ID: {self._video_id}"
            )
            return Message(text=success_msg)

        except (ValueError, KeyError, IndexCreationError, TaskError, TaskTimeoutError) as e:
            self.log(f"Error: {e!s}", "ERROR")
            # 注意：失败时清空缓存 ID，避免后续误用
            self._video_id = None
            self._index_id = None
            self._task_id = None
            return Message(text=f"Error: {e!s}")

    def get_video_id(self) -> Message:
        """返回当前缓存的 `video_id`（无则为空字符串）。"""
        video_id = self._video_id or ""
        return Message(text=video_id)
