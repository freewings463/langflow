"""
模块名称：视频文件输入组件

本模块将本地视频文件路径封装为 `Data`，并校验文件存在性与扩展名。
主要功能包括：
- 校验视频文件路径与格式
- 生成包含元数据的 `Data`/`DataFrame`

关键组件：
- `VideoFileComponent`

设计背景：为 TwelveLabs 视频索引提供统一的文件输入。
注意事项：Astra 云环境不允许本地文件访问。
"""

from pathlib import Path

from lfx.base.data import BaseFileComponent
from lfx.io import FileInput
from lfx.schema import Data, DataFrame
from lfx.utils.validate_cloud import raise_error_if_astra_cloud_disable_component

disable_component_in_astra_cloud_msg = (
    "Video processing is not supported in Astra cloud environment. "
    "Video components require local file system access for processing. "
    "Please use local storage mode or process videos locally before uploading."
)


class VideoFileComponent(BaseFileComponent):
    """视频文件输入组件。

    契约：
    - 输入：单个视频文件路径
    - 输出：包含 `text` 与 `metadata` 的 `DataFrame`
    - 副作用：访问本地文件系统并记录日志
    - 失败语义：路径不存在或扩展名不合法时抛异常/返回空结果
    """

    display_name = "Video File"
    description = "Load a video file in common video formats."
    icon = "TwelveLabs"
    name = "VideoFile"
    documentation = "https://github.com/twelvelabs-io/twelvelabs-developer-experience/blob/main/integrations/Langflow/TWELVE_LABS_COMPONENTS_README.md"

    VALID_EXTENSIONS = [
        # 常见视频格式
        "mp4",
        "avi",
        "mov",
        "mkv",
        "webm",
        "flv",
        "wmv",
        "mpg",
        "mpeg",
        "m4v",
        "3gp",
        "3g2",
        "m2v",
        # 专业视频格式
        "mxf",
        "dv",
        "vob",
        # 其他视频格式
        "ogv",
        "rm",
        "rmvb",
        "amv",
        "divx",
        "m2ts",
        "mts",
        "ts",
        "qt",
        "yuv",
        "y4m",
    ]

    inputs = [
        FileInput(
            display_name="Video File",
            name="file_path",
            file_types=[
                # 常见视频格式
                "mp4",
                "avi",
                "mov",
                "mkv",
                "webm",
                "flv",
                "wmv",
                "mpg",
                "mpeg",
                "m4v",
                "3gp",
                "3g2",
                "m2v",
                # 专业视频格式
                "mxf",
                "dv",
                "vob",
                # 其他视频格式
                "ogv",
                "rm",
                "rmvb",
                "amv",
                "divx",
                "m2ts",
                "mts",
                "ts",
                "qt",
                "yuv",
                "y4m",
            ],
            required=True,
            info="Upload a video file in any common video format supported by ffmpeg",
        ),
    ]

    outputs = [
        *BaseFileComponent.get_base_outputs(),
    ]

    def process_files(self, file_list: list[BaseFileComponent.BaseFile]) -> list[BaseFileComponent.BaseFile]:
        """校验并封装视频文件为 `Data`。

        契约：
        - 输入：文件对象列表
        - 输出：包含 `Data` 的文件列表
        - 副作用：访问本地文件系统并记录日志
        - 失败语义：缺失文件或扩展名不合法时抛异常
        """
        # 注意：Astra 云环境禁止本地文件访问
        raise_error_if_astra_cloud_disable_component(disable_component_in_astra_cloud_msg)
        self.log(f"DEBUG: Processing video files: {len(file_list)}")

        if not file_list:
            msg = "No files to process."
            raise ValueError(msg)

        processed_files = []
        for file in file_list:
            try:
                file_path = str(file.path)
                self.log(f"DEBUG: Processing video file: {file_path}")

                # 校验文件存在
                file_path_obj = Path(file_path)
                if not file_path_obj.exists():
                    error_msg = f"Video file not found: {file_path}"
                    raise FileNotFoundError(error_msg)

                # 校验扩展名
                if not file_path.lower().endswith(tuple(self.VALID_EXTENSIONS)):
                    error_msg = f"Invalid file type. Expected: {', '.join(self.VALID_EXTENSIONS)}"
                    raise ValueError(error_msg)

                # 组装 Data 所需结构
                doc_data = {"text": file_path, "metadata": {"source": file_path, "type": "video"}}

                # 将字典写入 Data
                file.data = Data(data=doc_data)

                self.log(f"DEBUG: Created data: {doc_data}")
                processed_files.append(file)

            except Exception as e:
                self.log(f"Error processing video file: {e!s}", "ERROR")
                raise

        return processed_files

    def load_files(self) -> DataFrame:
        """加载视频文件并返回 `DataFrame`。

        契约：
        - 输入：`file_path`
        - 输出：`DataFrame`（可能为空）
        - 副作用：读取本地文件并记录日志
        - 失败语义：文件错误或解析异常时返回空 `DataFrame`
        """
        # 注意：Astra 云环境禁止本地文件访问
        raise_error_if_astra_cloud_disable_component(disable_component_in_astra_cloud_msg)

        try:
            self.log("DEBUG: Starting video file load")
            if not hasattr(self, "file_path") or not self.file_path:
                self.log("DEBUG: No video file path provided")
                return DataFrame()

            self.log(f"DEBUG: Loading video from path: {self.file_path}")

            # 校验文件存在
            file_path_obj = Path(self.file_path)
            if not file_path_obj.exists():
                self.log(f"DEBUG: Video file not found at path: {self.file_path}")
                return DataFrame()

            # 读取文件大小
            file_size = file_path_obj.stat().st_size
            self.log(f"DEBUG: Video file size: {file_size} bytes")

            # 组装 Data 结构
            video_data = {
                "text": self.file_path,
                "metadata": {"source": self.file_path, "type": "video", "size": file_size},
            }

            self.log(f"DEBUG: Created video data: {video_data}")
            result = DataFrame(data=[video_data])

            # 记录返回结果
            self.log("DEBUG: Returning list with Data objects")
        except (FileNotFoundError, PermissionError, OSError) as e:
            self.log(f"DEBUG: File error in video load_files: {e!s}", "ERROR")
            return DataFrame()
        except ImportError as e:
            self.log(f"DEBUG: Import error in video load_files: {e!s}", "ERROR")
            return DataFrame()
        except (ValueError, TypeError) as e:
            self.log(f"DEBUG: Value or type error in video load_files: {e!s}", "ERROR")
            return DataFrame()
        else:
            return result
