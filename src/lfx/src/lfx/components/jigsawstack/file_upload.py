"""
模块名称：JigsawStack 文件上传组件

本模块将本地文件上传到 JigsawStack File Storage，并返回存储结果。
主要功能包括：
- 读取本地文件并按二进制上传
- 支持自定义 `key`、覆盖策略与临时公开链接
- 统一处理上传失败并返回错误信息

关键组件：
- JigsawStackFileUploadComponent：文件上传组件入口

设计背景：为 Langflow 提供标准化的文件存储能力。
注意事项：上传涉及外部网络调用，失败时返回 `success=False`。
"""

from pathlib import Path

from lfx.custom.custom_component.component import Component
from lfx.io import BoolInput, FileInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data


class JigsawStackFileUploadComponent(Component):
    """JigsawStack 文件上传组件封装。

    契约：输入为本地 `file` 路径及可选 `key`/覆盖策略；输出 `Data`。
    副作用：读取本地文件并发起网络上传。
    失败语义：SDK 缺失抛 `ImportError`；SDK 异常返回失败 `Data`。
    """

    display_name = "File Upload"
    description = "Store any file seamlessly on JigsawStack File Storage and use it in your AI applications. \
        Supports various file types including images, documents, and more."
    documentation = "https://jigsawstack.com/docs/api-reference/store/file/add"
    icon = "JigsawStack"
    name = "JigsawStackFileUpload"

    inputs = [
        SecretStrInput(
            name="api_key",
            display_name="JigsawStack API Key",
            info="Your JigsawStack API key for authentication",
            required=True,
        ),
        FileInput(
            name="file",
            display_name="File",
            info="Upload file to be stored on JigsawStack File Storage.",
            required=True,
            file_types=["pdf", "png", "jpg", "jpeg", "mp4", "mp3", "txt", "docx", "xlsx"],
        ),
        StrInput(
            name="key",
            display_name="Key",
            info="The key used to store the file on JigsawStack File Storage. \
                If not provided, a unique key will be generated.",
            required=False,
            tool_mode=True,
        ),
        BoolInput(
            name="overwrite",
            display_name="Overwrite Existing File",
            info="If true, will overwrite the existing file with the same key. \
                If false, will return an error if the file already exists.",
            required=False,
            value=True,
        ),
        BoolInput(
            name="temp_public_url",
            display_name="Return Temporary Public URL",
            info="If true, will return a temporary public URL which lasts for a limited time. \
                If false, will return the file store key which can only be accessed by the owner.",
            required=False,
            value=False,
            tool_mode=True,
        ),
    ]

    outputs = [
        Output(display_name="File Store Result", name="file_upload_result", method="upload_file"),
    ]

    def upload_file(self) -> Data:
        """上传文件到 JigsawStack File Storage。

        契约：输入为本地 `file` 与可选 `key` 参数；输出为 `Data`。
        副作用：读取本地文件并发起网络上传。
        失败语义：SDK 异常返回失败 `Data`；SDK 缺失抛 `ImportError`。

        关键路径（三步）：
        1) 读取本地文件内容；
        2) 组装上传参数（`key`/`overwrite`/`temp_public_url`）；
        3) 调用 `client.store.upload` 并返回结果。

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

            file_path = Path(self.file)
            with Path.open(file_path, "rb") as f:
                file_content = f.read()
            params = {}

            if self.key:
                params["key"] = self.key
            if self.overwrite is not None:
                params["overwrite"] = self.overwrite
            if self.temp_public_url is not None:
                params["temp_public_url"] = self.temp_public_url

            response = client.store.upload(file_content, params)
            return Data(data=response)

        except JigsawStackError as e:
            error_data = {"error": str(e), "success": False}
            self.status = f"Error: {e!s}"
            return Data(data=error_data)
