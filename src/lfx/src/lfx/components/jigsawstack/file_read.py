"""
模块名称：JigsawStack 文件读取组件

本模块从 JigsawStack File Storage 拉取文件并落盘到临时文件，返回本地路径供下游使用。
主要功能包括：
- 通过 `key` 拉取文件内容
- 基于魔数推断常见扩展名
- 生成临时文件并返回路径

关键组件：
- JigsawStackFileReadComponent：读取并落盘文件

设计背景：为 Langflow 提供统一的文件读取接口。
注意事项：生成的临时文件不会自动删除，调用方需自行清理。
"""

import tempfile

from lfx.custom.custom_component.component import Component
from lfx.io import Output, SecretStrInput, StrInput
from lfx.schema.data import Data


class JigsawStackFileReadComponent(Component):
    """JigsawStack 文件读取组件封装。

    契约：输入为 `key`；输出 `Data`，包含 `file_path` 与元信息。
    副作用：写入本地临时文件并更新 `self.status`。
    失败语义：`key` 为空抛 `ValueError`；SDK 缺失抛 `ImportError`；SDK 异常返回失败 `Data`。
    """

    display_name = "File Read"
    description = "Read any previously uploaded file seamlessly from \
        JigsawStack File Storage and use it in your AI applications."
    documentation = "https://jigsawstack.com/docs/api-reference/store/file/get"
    icon = "JigsawStack"
    name = "JigsawStackFileRead"

    inputs = [
        SecretStrInput(
            name="api_key",
            display_name="JigsawStack API Key",
            info="Your JigsawStack API key for authentication",
            required=True,
        ),
        StrInput(
            name="key",
            display_name="Key",
            info="The key used to retrieve the file from JigsawStack File Storage.",
            required=True,
            tool_mode=True,
        ),
    ]

    outputs = [
        Output(display_name="File Path", name="file_path", method="read_and_save_file"),
    ]

    def read_and_save_file(self) -> Data:
        """读取远端文件并保存到临时目录。

        契约：输入为 `key`，输出 `Data`（含 `file_path` 与元信息）。
        副作用：创建本地临时文件（不会自动删除）。
        失败语义：`key` 为空抛 `ValueError`；SDK 异常返回失败 `Data`。

        关键路径（三步）：
        1) 校验 `key` 并调用 `client.store.get`；
        2) 通过魔数推断扩展名；
        3) 写入临时文件并返回路径与元信息。

        """
        try:
            from jigsawstack import JigsawStack, JigsawStackError
        except ImportError as e:
            jigsawstack_import_error = (
                "JigsawStack package not found. Please install it using: pip install jigsawstack>=0.2.7"
            )
            raise ImportError(jigsawstack_import_error) from e

        try:
            client = JigsawStack(api_key=self.api_key)
            if not self.key or self.key.strip() == "":
                invalid_key_error = "Key is required to read a file from JigsawStack File Storage."
                raise ValueError(invalid_key_error)

            # 实现：拉取远端文件内容
            response = client.store.get(self.key)

            # 实现：基于内容推断扩展名，便于下游识别类型
            file_extension = self._detect_file_extension(response)

            # 注意：临时文件不会自动删除，调用方应在使用后清理
            with tempfile.NamedTemporaryFile(
                delete=False, suffix=file_extension, prefix=f"jigsawstack_{self.key}_"
            ) as temp_file:
                if isinstance(response, bytes):
                    temp_file.write(response)
                else:
                    # 实现：字符串内容以 UTF-8 编码写入
                    temp_file.write(response.encode("utf-8"))

                temp_path = temp_file.name

            return Data(
                data={
                    "file_path": temp_path,
                    "key": self.key,
                    "file_extension": file_extension,
                    "size": len(response) if isinstance(response, bytes) else len(str(response)),
                    "success": True,
                }
            )

        except JigsawStackError as e:
            error_data = {"error": str(e), "success": False}
            self.status = f"Error: {e!s}"
            return Data(data=error_data)

    def _detect_file_extension(self, content) -> str:
        """基于内容推断扩展名。

        契约：输入为 `bytes` 或 `str`；输出常见扩展名（未知则 `.bin`）。
        失败语义：不抛异常，无法识别时返回 `.bin` 或 `.txt`。
        """
        if isinstance(content, bytes):
            # 实现：按常见魔数识别图片/文档/媒体类型
            if content.startswith(b"\xff\xd8\xff"):
                return ".jpg"
            if content.startswith(b"\x89PNG\r\n\x1a\n"):
                return ".png"
            if content.startswith((b"GIF87a", b"GIF89a")):
                return ".gif"
            if content.startswith(b"%PDF"):
                return ".pdf"
            if content.startswith(b"PK\x03\x04"):
                # 实现：识别 ZIP/Office 文档容器
                return ".zip"
            if content.startswith(b"\x00\x00\x01\x00"):
                # 实现：识别 ICO 图标文件
                return ".ico"
            if content.startswith(b"RIFF") and b"WEBP" in content[:12]:
                return ".webp"
            if content.startswith((b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")):
                return ".mp3"
            if content.startswith((b"ftypmp4", b"\x00\x00\x00\x20ftypmp4")):
                return ".mp4"
            # 实现：可解码则按文本处理
            try:
                content.decode("utf-8")
                return ".txt"  # noqa: TRY300
            except UnicodeDecodeError:
                # 注意：无法识别类型时按二进制处理
                return ".bin"
        else:
            return ".txt"
