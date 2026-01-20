"""
模块名称：文件读取组件（含 Docling）

本模块提供文件读取与解析能力，支持本地/云存储，并在高级模式下通过 Docling 子进程解析复杂格式。
主要功能包括：
- 支持本地、S3 与 Google Drive 的文件读取
- 根据文件类型在标准解析与 Docling 解析之间切换
- 在工具模式下提供无参读取能力

关键组件：
- FileComponent：文件读取与解析入口

设计背景：将 Docling 的重型解析隔离到子进程，避免主进程内存增长与状态污染。
注意事项：Astra Cloud 禁用高级解析；部分扩展仅在高级模式下可用。
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import textwrap
from copy import deepcopy
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from lfx.base.data.base_file import BaseFileComponent
from lfx.base.data.storage_utils import parse_storage_path, read_file_bytes, validate_image_content_type
from lfx.base.data.utils import TEXT_FILE_TYPES, parallel_load_data, parse_text_file_to_data
from lfx.inputs import SortableListInput
from lfx.inputs.inputs import DropdownInput, MessageTextInput, StrInput
from lfx.io import BoolInput, FileInput, IntInput, Output, SecretStrInput
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame  # noqa: TC001
from lfx.schema.message import Message
from lfx.services.deps import get_settings_service, get_storage_service
from lfx.utils.async_helpers import run_until_complete
from lfx.utils.validate_cloud import is_astra_cloud_environment


def _get_storage_location_options():
    """获取存储位置选项（云环境隐藏本地存储）。"""
    all_options = [{"name": "AWS", "icon": "Amazon"}, {"name": "Google Drive", "icon": "google"}]
    if is_astra_cloud_environment():
        return all_options
    return [{"name": "Local", "icon": "hard-drive"}, *all_options]


class FileComponent(BaseFileComponent):
    """文件读取组件（可选 Docling 子进程解析）。

    契约：输入文件路径来自上传或云存储；高级模式要求 Docling 兼容扩展名。
    副作用：读取文件、可能触发子进程解析与云存储下载。
    失败语义：参数缺失/解析失败会抛 `ValueError`/`RuntimeError` 或返回错误 `Data`。
    """

    display_name = "Read File"
    # 注意：`description` 为动态属性，见 `get_tool_description`
    _base_description = "Loads content from one or more files."
    documentation: str = "https://docs.langflow.org/read-file"
    icon = "file-text"
    name = "File"
    add_tool_output = True  # 启用工具模式切换，无需 tool_mode 输入

    # 无需 Docling 的扩展（标准文本解析）
    TEXT_EXTENSIONS = TEXT_FILE_TYPES

    # 需 Docling 处理的扩展（图片/高级办公格式等）
    DOCLING_ONLY_EXTENSIONS = [
        "adoc",
        "asciidoc",
        "asc",
        "bmp",
        "dotx",
        "dotm",
        "docm",
        "jpg",
        "jpeg",
        "png",
        "potx",
        "ppsx",
        "pptm",
        "potm",
        "ppsm",
        "pptx",
        "tiff",
        "xls",
        "xlsx",
        "xhtml",
        "webp",
    ]

    # Docling 兼容扩展（基础加载支持 TEXT_FILE_TYPES）
    VALID_EXTENSIONS = [
        *TEXT_EXTENSIONS,
        *DOCLING_ONLY_EXTENSIONS,
    ]

    # Markdown 导出固定配置
    EXPORT_FORMAT = "Markdown"
    IMAGE_MODE = "placeholder"

    _base_inputs = deepcopy(BaseFileComponent.get_base_inputs())

    for input_item in _base_inputs:
        if isinstance(input_item, FileInput) and input_item.name == "path":
            input_item.real_time_refresh = True
            input_item.tool_mode = False  # 关闭上传输入的工具模式
            input_item.required = False  # 工具模式下允许为空
            break

    inputs = [
        SortableListInput(
            name="storage_location",
            display_name="Storage Location",
            placeholder="Select Location",
            info="Choose where to read the file from.",
            options=_get_storage_location_options(),
            real_time_refresh=True,
            limit=1,
        ),
        *_base_inputs,
        StrInput(
            name="file_path_str",
            display_name="File Path",
            info=(
                "Path to the file to read. Used when component is called as a tool. "
                "If not provided, will use the uploaded file from 'path' input."
            ),
            show=False,
            advanced=True,
            tool_mode=True,  # 仅用于工具开关，_get_tools() 会忽略该参数
            required=False,
        ),
        # AWS S3 专属输入
        SecretStrInput(
            name="aws_access_key_id",
            display_name="AWS Access Key ID",
            info="AWS Access key ID.",
            show=False,
            advanced=False,
            required=True,
        ),
        SecretStrInput(
            name="aws_secret_access_key",
            display_name="AWS Secret Key",
            info="AWS Secret Key.",
            show=False,
            advanced=False,
            required=True,
        ),
        StrInput(
            name="bucket_name",
            display_name="S3 Bucket Name",
            info="Enter the name of the S3 bucket.",
            show=False,
            advanced=False,
            required=True,
        ),
        StrInput(
            name="aws_region",
            display_name="AWS Region",
            info="AWS region (e.g., us-east-1, eu-west-1).",
            show=False,
            advanced=False,
        ),
        StrInput(
            name="s3_file_key",
            display_name="S3 File Key",
            info="The key (path) of the file in S3 bucket.",
            show=False,
            advanced=False,
            required=True,
        ),
        # Google Drive 专属输入
        SecretStrInput(
            name="service_account_key",
            display_name="GCP Credentials Secret Key",
            info="Your Google Cloud Platform service account JSON key as a secret string (complete JSON content).",
            show=False,
            advanced=False,
            required=True,
        ),
        StrInput(
            name="file_id",
            display_name="Google Drive File ID",
            info=("The Google Drive file ID to read. The file must be shared with the service account email."),
            show=False,
            advanced=False,
            required=True,
        ),
        BoolInput(
            name="advanced_mode",
            display_name="Advanced Parser",
            value=False,
            real_time_refresh=True,
            info=(
                "Enable advanced document processing and export with Docling for PDFs, images, and office documents. "
                "Note that advanced document processing can consume significant resources."
            ),
            # 云环境禁用
            show=not is_astra_cloud_environment(),
        ),
        DropdownInput(
            name="pipeline",
            display_name="Pipeline",
            info="Docling pipeline to use",
            options=["standard", "vlm"],
            value="standard",
            advanced=True,
            real_time_refresh=True,
        ),
        DropdownInput(
            name="ocr_engine",
            display_name="OCR Engine",
            info="OCR engine to use. Only available when pipeline is set to 'standard'.",
            options=["None", "easyocr"],
            value="easyocr",
            show=False,
            advanced=True,
        ),
        StrInput(
            name="md_image_placeholder",
            display_name="Image placeholder",
            info="Specify the image placeholder for markdown exports.",
            value="<!-- image -->",
            advanced=True,
            show=False,
        ),
        StrInput(
            name="md_page_break_placeholder",
            display_name="Page break placeholder",
            info="Add this placeholder between pages in the markdown output.",
            value="",
            advanced=True,
            show=False,
        ),
        MessageTextInput(
            name="doc_key",
            display_name="Doc Key",
            info="The key to use for the DoclingDocument column.",
            value="doc",
            advanced=True,
            show=False,
        ),
        # 兼容保留的已弃用输入
        BoolInput(
            name="use_multithreading",
            display_name="[Deprecated] Use Multithreading",
            advanced=True,
            value=True,
            info="Set 'Processing Concurrency' greater than 1 to enable multithreading.",
        ),
        IntInput(
            name="concurrency_multithreading",
            display_name="Processing Concurrency",
            advanced=True,
            info="When multiple files are being processed, the number of files to process concurrently.",
            value=1,
        ),
        BoolInput(
            name="markdown",
            display_name="Markdown Export",
            info="Export processed documents to Markdown format. Only available when advanced mode is enabled.",
            value=False,
            show=False,
        ),
    ]

    outputs = [
        Output(display_name="Raw Content", name="message", method="load_files_message", tool_mode=True),
    ]

    # ------------------------------ 工具描述（包含文件名）--------------

    def get_tool_description(self) -> str:
        """返回包含已上传文件名的动态描述。"""
        base_description = "Loads and returns the content from uploaded files."

        # 获取已上传文件路径列表
        file_paths = getattr(self, "path", None)
        if not file_paths:
            return base_description

        # 统一为列表
        if not isinstance(file_paths, list):
            file_paths = [file_paths]

        # 提取文件名
        file_names = []
        for fp in file_paths:
            if fp:
                name = Path(fp).name
                file_names.append(name)

        if file_names:
            files_str = ", ".join(file_names)
            return f"{base_description} Available files: {files_str}. Call this tool to read these files."

        return base_description

    @property
    def description(self) -> str:
        """动态描述属性（包含已上传文件名）。"""
        return self.get_tool_description()

    async def _get_tools(self) -> list:
        """构建无参工具，直接读取已上传文件。"""
        from langchain_core.tools import StructuredTool
        from pydantic import BaseModel

        # 空参数模型：不接受外部路径
        class EmptySchema(BaseModel):
            """无参数模型：使用预上传文件。"""

        async def read_files_tool() -> str:
            """读取已上传文件内容。"""
            try:
                result = self.load_files_message()
                if hasattr(result, "get_text"):
                    return result.get_text()
                if hasattr(result, "text"):
                    return result.text
                return str(result)
            except (FileNotFoundError, ValueError, OSError, RuntimeError) as e:
                return f"Error reading files: {e}"

        description = self.get_tool_description()

        tool = StructuredTool(
            name="load_files_message",
            description=description,
            coroutine=read_files_tool,
            args_schema=EmptySchema,
            handle_tool_error=True,
            tags=["load_files_message"],
            metadata={
                "display_name": "Read File",
                "display_description": description,
            },
        )

        return [tool]

    # ------------------------------ UI 辅助方法 --------------------------------------

    def _path_value(self, template: dict) -> list[str]:
        """从模板中读取当前选中的文件路径。"""
        return template.get("path", {}).get("file_path", [])

    def _disable_docling_fields_in_cloud(self, build_config: dict[str, Any]) -> None:
        """在云环境禁用所有 Docling 相关字段。"""
        if "advanced_mode" in build_config:
            build_config["advanced_mode"]["show"] = False
            build_config["advanced_mode"]["value"] = False
        # 隐藏 Docling 相关字段
        docling_fields = ("pipeline", "ocr_engine", "doc_key", "md_image_placeholder", "md_page_break_placeholder")
        for field in docling_fields:
            if field in build_config:
                build_config[field]["show"] = False
        # 同时禁用 OCR 引擎
        if "ocr_engine" in build_config:
            build_config["ocr_engine"]["value"] = "None"

    def update_build_config(
        self,
        build_config: dict[str, Any],
        field_value: Any,
        field_name: str | None = None,
    ) -> dict[str, Any]:
        """根据选择项展示/隐藏高级解析相关字段。

        关键路径（三步）：
        1) 刷新存储位置选项并处理选择。
        2) 根据存储位置切换输入字段可见性。
        3) 按文件类型与高级模式开关显示 Docling 相关字段。

        异常流：无显式抛错，错误由上游字段校验处理。
        排障入口：检查 `build_config` 中 `advanced_mode/pipeline/ocr_engine` 的可见状态。
        """
        # 根据云环境动态刷新存储选项
        if "storage_location" in build_config:
            updated_options = _get_storage_location_options()
            build_config["storage_location"]["options"] = updated_options

        # 处理存储位置选择
        if field_name == "storage_location":
            # 提取所选存储位置
            selected = [location["name"] for location in field_value] if isinstance(field_value, list) else []

            # 先隐藏所有存储相关字段
            storage_fields = [
                "aws_access_key_id",
                "aws_secret_access_key",
                "bucket_name",
                "aws_region",
                "s3_file_key",
                "service_account_key",
                "file_id",
            ]

            for f_name in storage_fields:
                if f_name in build_config:
                    build_config[f_name]["show"] = False

            # 根据存储位置显示字段
            if len(selected) == 1:
                location = selected[0]

                if location == "Local":
                    # 本地存储：显示上传输入
                    if "path" in build_config:
                        build_config["path"]["show"] = True

                elif location == "AWS":
                    # AWS：隐藏上传输入，显示 AWS 字段
                    if "path" in build_config:
                        build_config["path"]["show"] = False

                    aws_fields = [
                        "aws_access_key_id",
                        "aws_secret_access_key",
                        "bucket_name",
                        "aws_region",
                        "s3_file_key",
                    ]
                    for f_name in aws_fields:
                        if f_name in build_config:
                            build_config[f_name]["show"] = True
                            build_config[f_name]["advanced"] = False

                elif location == "Google Drive":
                    # Google Drive：隐藏上传输入，显示 Drive 字段
                    if "path" in build_config:
                        build_config["path"]["show"] = False

                    gdrive_fields = ["service_account_key", "file_id"]
                    for f_name in gdrive_fields:
                        if f_name in build_config:
                            build_config[f_name]["show"] = True
                            build_config[f_name]["advanced"] = False
            # 未选择存储位置：默认显示上传输入
            elif "path" in build_config:
                build_config["path"]["show"] = True

            return build_config

        if field_name == "path":
            paths = self._path_value(build_config)

            # 云环境禁用 Docling
            if is_astra_cloud_environment():
                self._disable_docling_fields_in_cloud(build_config)
            else:
                # 仅当全部文件可由 Docling 处理时展示高级模式
                allow_advanced = all(not file_path.endswith((".csv", ".xlsx", ".parquet")) for file_path in paths)
                build_config["advanced_mode"]["show"] = allow_advanced
                if not allow_advanced:
                    build_config["advanced_mode"]["value"] = False
                    docling_fields = (
                        "pipeline",
                        "ocr_engine",
                        "doc_key",
                        "md_image_placeholder",
                        "md_page_break_placeholder",
                    )
                    for field in docling_fields:
                        if field in build_config:
                            build_config[field]["show"] = False

        # Docling 处理逻辑
        elif field_name == "advanced_mode":
            # 云环境：无论开关状态均隐藏 Docling 字段
            if is_astra_cloud_environment():
                self._disable_docling_fields_in_cloud(build_config)
            else:
                docling_fields = (
                    "pipeline",
                    "ocr_engine",
                    "doc_key",
                    "md_image_placeholder",
                    "md_page_break_placeholder",
                )
                for field in docling_fields:
                    if field in build_config:
                        build_config[field]["show"] = bool(field_value)
                        if field == "pipeline":
                            build_config[field]["advanced"] = not bool(field_value)

        elif field_name == "pipeline":
            # 云环境：不显示 OCR 选项
            if is_astra_cloud_environment():
                self._disable_docling_fields_in_cloud(build_config)
            elif field_value == "standard":
                build_config["ocr_engine"]["show"] = True
                build_config["ocr_engine"]["value"] = "easyocr"
            else:
                build_config["ocr_engine"]["show"] = False
                build_config["ocr_engine"]["value"] = "None"

        return build_config

    def update_outputs(self, frontend_node: dict[str, Any], field_name: str, field_value: Any) -> dict[str, Any]:  # noqa: ARG002
        """根据文件数量/类型与高级模式动态输出端口。"""
        if field_name not in ["path", "advanced_mode", "pipeline"]:
            return frontend_node

        template = frontend_node.get("template", {})
        paths = self._path_value(template)
        if not paths:
            return frontend_node

        frontend_node["outputs"] = []
        if len(paths) == 1:
            file_path = paths[0] if field_name == "path" else frontend_node["template"]["path"]["file_path"][0]
            if file_path.endswith((".csv", ".xlsx", ".parquet")):
                frontend_node["outputs"].append(
                    Output(
                        display_name="Structured Content",
                        name="dataframe",
                        method="load_files_structured",
                        tool_mode=True,
                    ),
                )
            elif file_path.endswith(".json"):
                frontend_node["outputs"].append(
                    Output(display_name="Structured Content", name="json", method="load_files_json", tool_mode=True),
                )

            advanced_mode = frontend_node.get("template", {}).get("advanced_mode", {}).get("value", False)
            if advanced_mode:
                frontend_node["outputs"].append(
                    Output(
                        display_name="Structured Output",
                        name="advanced_dataframe",
                        method="load_files_dataframe",
                        tool_mode=True,
                    ),
                )
                frontend_node["outputs"].append(
                    Output(
                        display_name="Markdown", name="advanced_markdown", method="load_files_markdown", tool_mode=True
                    ),
                )
                frontend_node["outputs"].append(
                    Output(display_name="File Path", name="path", method="load_files_path", tool_mode=True),
                )
            else:
                frontend_node["outputs"].append(
                    Output(display_name="Raw Content", name="message", method="load_files_message", tool_mode=True),
                )
                frontend_node["outputs"].append(
                    Output(display_name="File Path", name="path", method="load_files_path", tool_mode=True),
                )
        else:
            # 多文件：仅输出 DataFrame，并禁用高级解析
            frontend_node["outputs"].append(
                Output(display_name="Files", name="dataframe", method="load_files", tool_mode=True)
            )

        return frontend_node

    # ------------------------------ 核心处理 ----------------------------------

    def _get_selected_storage_location(self) -> str:
        """从选择器中获取存储位置。"""
        if hasattr(self, "storage_location") and self.storage_location:
            if isinstance(self.storage_location, list) and len(self.storage_location) > 0:
                return self.storage_location[0].get("name", "")
            if isinstance(self.storage_location, dict):
                return self.storage_location.get("name", "")
        return "Local"  # 未配置时默认本地

    def _validate_and_resolve_paths(self) -> list[BaseFileComponent.BaseFile]:
        """解析输入路径，优先处理云存储与工具模式路径。"""
        storage_location = self._get_selected_storage_location()

        # AWS S3
        if storage_location == "AWS":
            return self._read_from_aws_s3()

        # Google Drive
        if storage_location == "Google Drive":
            return self._read_from_google_drive()

        # 本地存储：优先使用工具模式路径
        file_path_str = getattr(self, "file_path_str", None)
        if file_path_str:
            from pathlib import Path

            from lfx.schema.data import Data

            resolved_path = Path(self.resolve_path(file_path_str))
            if not resolved_path.exists():
                msg = f"File or directory not found: {file_path_str}"
                self.log(msg)
                if not self.silent_errors:
                    raise ValueError(msg)
                return []

            data_obj = Data(data={self.SERVER_FILE_PATH_FIELDNAME: str(resolved_path)})
            return [BaseFileComponent.BaseFile(data_obj, resolved_path, delete_after_processing=False)]

        # 否则使用默认实现（FileInput 上传路径）
        return super()._validate_and_resolve_paths()

    def _read_from_aws_s3(self) -> list[BaseFileComponent.BaseFile]:
        """从 AWS S3 读取文件。"""
        from lfx.base.data.cloud_storage_utils import create_s3_client, validate_aws_credentials

        # 校验 AWS 凭据
        validate_aws_credentials(self)
        if not getattr(self, "s3_file_key", None):
            msg = "S3 File Key is required"
            raise ValueError(msg)

        # 创建 S3 客户端
        s3_client = create_s3_client(self)

        import tempfile

        # 从 S3 key 推断扩展名
        file_extension = Path(self.s3_file_key).suffix or ""

        with tempfile.NamedTemporaryFile(mode="wb", suffix=file_extension, delete=False) as temp_file:
            temp_file_path = temp_file.name
            try:
                s3_client.download_fileobj(self.bucket_name, self.s3_file_key, temp_file)
            except Exception as e:
                # 失败时清理临时文件
                with contextlib.suppress(OSError):
                    Path(temp_file_path).unlink()
                msg = f"Failed to download file from S3: {e}"
                raise RuntimeError(msg) from e

        # 构建 BaseFile
        from lfx.schema.data import Data

        temp_path = Path(temp_file_path)
        data_obj = Data(data={self.SERVER_FILE_PATH_FIELDNAME: str(temp_path)})
        return [BaseFileComponent.BaseFile(data_obj, temp_path, delete_after_processing=True)]

    def _read_from_google_drive(self) -> list[BaseFileComponent.BaseFile]:
        """从 Google Drive 读取文件。"""
        import tempfile

        from googleapiclient.http import MediaIoBaseDownload

        from lfx.base.data.cloud_storage_utils import create_google_drive_service

        # 校验 Google Drive 凭据
        if not getattr(self, "service_account_key", None):
            msg = "GCP Credentials Secret Key is required for Google Drive storage"
            raise ValueError(msg)
        if not getattr(self, "file_id", None):
            msg = "Google Drive File ID is required"
            raise ValueError(msg)

        # 创建只读 Google Drive 服务
        drive_service = create_google_drive_service(
            self.service_account_key, scopes=["https://www.googleapis.com/auth/drive.readonly"]
        )

        # 获取文件元信息以确定名称与扩展名
        try:
            file_metadata = drive_service.files().get(fileId=self.file_id, fields="name,mimeType").execute()
            file_name = file_metadata.get("name", "download")
        except Exception as e:
            msg = (
                f"Unable to access file with ID '{self.file_id}'. "
                f"Error: {e!s}. "
                "Please ensure: 1) The file ID is correct, 2) The file exists, "
                "3) The service account has been granted access to this file."
            )
            raise ValueError(msg) from e

        # 下载到临时文件
        file_extension = Path(file_name).suffix or ""
        with tempfile.NamedTemporaryFile(mode="wb", suffix=file_extension, delete=False) as temp_file:
            temp_file_path = temp_file.name
            try:
                request = drive_service.files().get_media(fileId=self.file_id)
                downloader = MediaIoBaseDownload(temp_file, request)
                done = False
                while not done:
                    _status, done = downloader.next_chunk()
            except Exception as e:
                # 失败时清理临时文件
                with contextlib.suppress(OSError):
                    Path(temp_file_path).unlink()
                msg = f"Failed to download file from Google Drive: {e}"
                raise RuntimeError(msg) from e

        # 构建 BaseFile
        from lfx.schema.data import Data

        temp_path = Path(temp_file_path)
        data_obj = Data(data={self.SERVER_FILE_PATH_FIELDNAME: str(temp_path)})
        return [BaseFileComponent.BaseFile(data_obj, temp_path, delete_after_processing=True)]

    def _is_docling_compatible(self, file_path: str) -> bool:
        """基于扩展名判断 Docling 兼容性。"""
        docling_exts = (
            ".adoc",
            ".asciidoc",
            ".asc",
            ".bmp",
            ".csv",
            ".dotx",
            ".dotm",
            ".docm",
            ".docx",
            ".htm",
            ".html",
            ".jpg",
            ".jpeg",
            ".json",
            ".md",
            ".pdf",
            ".png",
            ".potx",
            ".ppsx",
            ".pptm",
            ".potm",
            ".ppsm",
            ".pptx",
            ".tiff",
            ".txt",
            ".xls",
            ".xlsx",
            ".xhtml",
            ".xml",
            ".webp",
        )
        return file_path.lower().endswith(docling_exts)

    async def _get_local_file_for_docling(self, file_path: str) -> tuple[str, bool]:
        """获取 Docling 处理所需的本地文件路径（必要时从 S3 下载）。"""
        settings = get_settings_service().settings
        if settings.storage_type == "local":
            return file_path, False

        # S3 存储：下载至临时文件
        parsed = parse_storage_path(file_path)
        if not parsed:
            msg = f"Invalid S3 path format: {file_path}. Expected 'flow_id/filename'"
            raise ValueError(msg)

        storage_service = get_storage_service()
        flow_id, filename = parsed

        # 从 S3 获取文件内容
        content = await storage_service.get_file(flow_id, filename)

        suffix = Path(filename).suffix
        with NamedTemporaryFile(mode="wb", suffix=suffix, delete=False) as tmp_file:
            tmp_file.write(content)
            temp_path = tmp_file.name

        return temp_path, True

    def _process_docling_in_subprocess(self, file_path: str) -> Data | None:
        """在子进程运行 Docling 并映射为 `Data`。

        关键路径（三步）：
        1) 根据存储类型准备本地文件路径。
        2) 调用子进程解析并返回结构化结果。
        3) 清理临时文件并回传 `Data`。

        异常流：解析失败返回包含 `error` 的 `Data`，不在此处抛错。
        """
        if not file_path:
            return None

        settings = get_settings_service().settings
        if settings.storage_type == "s3":
            local_path, should_delete = run_until_complete(self._get_local_file_for_docling(file_path))
        else:
            local_path = file_path
            should_delete = False

        try:
            return self._process_docling_subprocess_impl(local_path, file_path)
        finally:
            # 如为临时文件则清理
            if should_delete:
                with contextlib.suppress(Exception):
                    Path(local_path).unlink()  # 忽略清理错误

    def _process_docling_subprocess_impl(self, local_file_path: str, original_file_path: str) -> Data | None:
        """Docling 子进程执行实现。

        关键路径（三步）：
        1) 组装配置并通过 stdin 传给子进程脚本。
        2) 解析子进程输出 JSON 并校验成功标志。
        3) 统一 `file_path` 并构造 `Data` 返回。

        异常流：子进程输出异常或 JSON 不合法时返回包含 `error` 的 `Data`。
        """
        args: dict[str, Any] = {
            "file_path": local_file_path,
            "markdown": bool(self.markdown),
            "image_mode": str(self.IMAGE_MODE),
            "md_image_placeholder": str(self.md_image_placeholder),
            "md_page_break_placeholder": str(self.md_page_break_placeholder),
            "pipeline": str(self.pipeline),
            "ocr_engine": (
                self.ocr_engine if self.ocr_engine and self.ocr_engine != "None" and self.pipeline != "vlm" else None
            ),
        }

        # 子进程脚本（隔离 Docling 处理）
        child_script = textwrap.dedent(
            r"""
            import json, sys

            def try_imports():
                try:
                    from docling.datamodel.base_models import ConversionStatus, InputFormat  # type: ignore
                    from docling.document_converter import DocumentConverter  # type: ignore
                    from docling_core.types.doc import ImageRefMode  # type: ignore
                    return ConversionStatus, InputFormat, DocumentConverter, ImageRefMode, "latest"
                except Exception as e:
                    raise e

            def create_converter(strategy, input_format, DocumentConverter, pipeline, ocr_engine):
                # --- 标准 PDF/图片流水线（含可选 OCR）---
                if pipeline == "standard":
                    try:
                        from docling.datamodel.pipeline_options import PdfPipelineOptions  # type: ignore
                        from docling.document_converter import PdfFormatOption  # type: ignore

                        pipe = PdfPipelineOptions()
                        pipe.do_ocr = False

                        if ocr_engine:
                            try:
                                from docling.models.factories import get_ocr_factory  # type: ignore
                                pipe.do_ocr = True
                                fac = get_ocr_factory(allow_external_plugins=False)
                                pipe.ocr_options = fac.create_options(kind=ocr_engine)
                            except Exception:
                                # OCR 初始化失败则关闭
                                pipe.do_ocr = False

                        fmt = {}
                        if hasattr(input_format, "PDF"):
                            fmt[getattr(input_format, "PDF")] = PdfFormatOption(pipeline_options=pipe)
                        if hasattr(input_format, "IMAGE"):
                            fmt[getattr(input_format, "IMAGE")] = PdfFormatOption(pipeline_options=pipe)

                        return DocumentConverter(format_options=fmt)
                    except Exception:
                        return DocumentConverter()

                # --- 视觉语言模型（VLM）流水线 ---
                if pipeline == "vlm":
                    try:
                        from docling.datamodel.pipeline_options import VlmPipelineOptions
                        from docling.datamodel.vlm_model_specs import GRANITEDOCLING_MLX, GRANITEDOCLING_TRANSFORMERS
                        from docling.document_converter import PdfFormatOption
                        from docling.pipeline.vlm_pipeline import VlmPipeline

                        vl_pipe = VlmPipelineOptions(
                            vlm_options=GRANITEDOCLING_TRANSFORMERS,
                        )

                        if sys.platform == "darwin":
                            try:
                                import mlx_vlm
                                vl_pipe.vlm_options = GRANITEDOCLING_MLX
                            except ImportError as e:
                                raise e

                        # VLM 通常不需要 OCR，默认关闭
                        fmt = {}
                        if hasattr(input_format, "PDF"):
                            fmt[getattr(input_format, "PDF")] = PdfFormatOption(
                            pipeline_cls=VlmPipeline,
                            pipeline_options=vl_pipe
                        )
                        if hasattr(input_format, "IMAGE"):
                            fmt[getattr(input_format, "IMAGE")] = PdfFormatOption(
                            pipeline_cls=VlmPipeline,
                            pipeline_options=vl_pipe
                        )

                        return DocumentConverter(format_options=fmt)
                    except Exception as e:
                        raise e

                # --- 回退：默认转换器 ---
                return DocumentConverter()

            def export_markdown(document, ImageRefMode, image_mode, img_ph, pg_ph):
                try:
                    mode = getattr(ImageRefMode, image_mode.upper(), image_mode)
                    return document.export_to_markdown(
                        image_mode=mode,
                        image_placeholder=img_ph,
                        page_break_placeholder=pg_ph,
                    )
                except Exception:
                    try:
                        return document.export_to_text()
                    except Exception:
                        return str(document)

            def to_rows(doc_dict):
                rows = []
                for t in doc_dict.get("texts", []):
                    prov = t.get("prov") or []
                    page_no = None
                    if prov and isinstance(prov, list) and isinstance(prov[0], dict):
                        page_no = prov[0].get("page_no")
                    rows.append({
                        "page_no": page_no,
                        "label": t.get("label"),
                        "text": t.get("text"),
                        "level": t.get("level"),
                    })
                return rows

            def main():
                cfg = json.loads(sys.stdin.read())
                file_path = cfg["file_path"]
                markdown = cfg["markdown"]
                image_mode = cfg["image_mode"]
                img_ph = cfg["md_image_placeholder"]
                pg_ph = cfg["md_page_break_placeholder"]
                pipeline = cfg["pipeline"]
                ocr_engine = cfg.get("ocr_engine")
                meta = {"file_path": file_path}

                try:
                    ConversionStatus, InputFormat, DocumentConverter, ImageRefMode, strategy = try_imports()
                    converter = create_converter(strategy, InputFormat, DocumentConverter, pipeline, ocr_engine)
                    try:
                        res = converter.convert(file_path)
                    except Exception as e:
                        print(json.dumps({"ok": False, "error": f"Docling conversion error: {e}", "meta": meta}))
                        return

                    ok = False
                    if hasattr(res, "status"):
                        try:
                            ok = (res.status == ConversionStatus.SUCCESS) or (str(res.status).lower() == "success")
                        except Exception:
                            ok = (str(res.status).lower() == "success")
                    if not ok and hasattr(res, "document"):
                        ok = getattr(res, "document", None) is not None
                    if not ok:
                        print(json.dumps({"ok": False, "error": "Docling conversion failed", "meta": meta}))
                        return

                    doc = getattr(res, "document", None)
                    if doc is None:
                        print(json.dumps({"ok": False, "error": "Docling produced no document", "meta": meta}))
                        return

                    if markdown:
                        text = export_markdown(doc, ImageRefMode, image_mode, img_ph, pg_ph)
                        print(json.dumps({"ok": True, "mode": "markdown", "text": text, "meta": meta}))
                        return

                    # 结构化输出
                    try:
                        doc_dict = doc.export_to_dict()
                    except Exception as e:
                        print(json.dumps({"ok": False, "error": f"Docling export_to_dict failed: {e}", "meta": meta}))
                        return

                    rows = to_rows(doc_dict)
                    print(json.dumps({"ok": True, "mode": "structured", "doc": rows, "meta": meta}))
                except Exception as e:
                    print(
                        json.dumps({
                            "ok": False,
                            "error": f"Docling processing error: {e}",
                            "meta": {"file_path": file_path},
                        })
                    )

            if __name__ == "__main__":
                main()
            """
        )

        # 校验 file_path，避免命令注入或不安全输入
        if not isinstance(args["file_path"], str) or any(c in args["file_path"] for c in [";", "|", "&", "$", "`"]):
            return Data(data={"error": "Unsafe file path detected.", "file_path": args["file_path"]})

        proc = subprocess.run(  # noqa: S603
            [sys.executable, "-u", "-c", child_script],
            input=json.dumps(args).encode("utf-8"),
            capture_output=True,
            check=False,
        )

        if not proc.stdout:
            err_msg = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else "no output from child process"
            return Data(data={"error": f"Docling subprocess error: {err_msg}", "file_path": original_file_path})

        try:
            result = json.loads(proc.stdout.decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            err_msg = proc.stderr.decode("utf-8", errors="replace")
            return Data(
                data={
                    "error": f"Invalid JSON from Docling subprocess: {e}. stderr={err_msg}",
                    "file_path": original_file_path,
                },
            )

        if not result.get("ok"):
            error_msg = result.get("error", "Unknown Docling error")
            # 覆盖 meta 的 file_path，确保与原路径匹配
            meta = result.get("meta", {})
            meta["file_path"] = original_file_path
            return Data(data={"error": error_msg, **meta})

        meta = result.get("meta", {})
        # 覆盖 meta 的 file_path，避免临时路径影响聚合
        # 子进程返回临时路径，但 rollup_data 需要原始 S3/本地路径
        meta["file_path"] = original_file_path
        if result.get("mode") == "markdown":
            exported_content = str(result.get("text", ""))
            return Data(
                text=exported_content,
                data={"exported_content": exported_content, "export_format": self.EXPORT_FORMAT, **meta},
            )

        rows = list(result.get("doc", []))
        return Data(data={"doc": rows, "export_format": self.EXPORT_FORMAT, **meta})

    def process_files(
        self,
        file_list: list[BaseFileComponent.BaseFile],
    ) -> list[BaseFileComponent.BaseFile]:
        """处理输入文件列表并返回结果。

        关键路径（三步）：
        1) 校验输入与文件类型，必要时进行图片内容验证。
        2) 高级模式下走 Docling 子进程解析并展开结果。
        3) 否则走标准解析并按并发配置处理。

        异常流：不满足高级模式要求时抛 `ValueError`；解析失败记录到 `Data.error` 或抛错。
        性能瓶颈：Docling 子进程与并发读取为主要耗时点。
        """
        if not file_list:
            msg = "No files to process."
            raise ValueError(msg)

        # 校验图片内容与扩展名匹配，避免媒体类型错误
        image_extensions = {"jpeg", "jpg", "png", "gif", "webp", "bmp", "tiff"}
        settings = get_settings_service().settings
        for file in file_list:
            extension = file.path.suffix[1:].lower()
            if extension in image_extensions:
                # 按存储类型读取字节内容
                try:
                    if settings.storage_type == "s3":
                        # S3 存储通过存储服务读取字节
                        file_path_str = str(file.path)
                        content = run_until_complete(read_file_bytes(file_path_str))
                    else:
                        # 本地存储直接读取字节
                        content = file.path.read_bytes()

                    is_valid, error_msg = validate_image_content_type(
                        str(file.path),
                        content=content,
                    )
                    if not is_valid:
                        self.log(error_msg)
                        if not self.silent_errors:
                            raise ValueError(error_msg)
                except (OSError, FileNotFoundError) as e:
                    self.log(f"Could not read file for validation: {e}")
                    # 继续流程，后续由更明确的错误处理

        # 仅在高级模式开启时处理 Docling 专用扩展
        if not self.advanced_mode:
            for file in file_list:
                extension = file.path.suffix[1:].lower()
                if extension in self.DOCLING_ONLY_EXTENSIONS:
                    if is_astra_cloud_environment():
                        msg = (
                            f"File '{file.path.name}' has extension '.{extension}' which requires "
                            f"Advanced Parser mode. Advanced Parser is not available in cloud environments."
                        )
                    else:
                        msg = (
                            f"File '{file.path.name}' has extension '.{extension}' which requires "
                            f"Advanced Parser mode. Please enable 'Advanced Parser' to process this file."
                        )
                    self.log(msg)
                    raise ValueError(msg)

        def process_file_standard(file_path: str, *, silent_errors: bool = False) -> Data | None:
            try:
                return parse_text_file_to_data(file_path, silent_errors=silent_errors)
            except FileNotFoundError as e:
                self.log(f"File not found: {file_path}. Error: {e}")
                if not silent_errors:
                    raise
                return None
            except Exception as e:
                self.log(f"Unexpected error processing {file_path}: {e}")
                if not silent_errors:
                    raise
                return None

        docling_compatible = all(self._is_docling_compatible(str(f.path)) for f in file_list)

        # 高级路径：仅当全部文件兼容 Docling 时启用
        if self.advanced_mode and docling_compatible:
            final_return: list[BaseFileComponent.BaseFile] = []
            for file in file_list:
                file_path = str(file.path)
                advanced_data: Data | None = self._process_docling_in_subprocess(file_path)

                # Docling 返回 None 时视为失败并降级
                if advanced_data is None:
                    error_data = Data(
                        data={
                            "file_path": file_path,
                            "error": "Docling processing returned no result. Check logs for details.",
                        },
                    )
                    final_return.extend(self.rollup_data([file], [error_data]))
                    continue

                # --- 展开：将 `doc` 中每个元素展开为独立 Data 行
                payload = getattr(advanced_data, "data", {}) or {}

                # 优先处理错误返回
                if "error" in payload:
                    error_msg = payload.get("error", "Unknown error")
                    error_data = Data(
                        data={
                            "file_path": file_path,
                            "error": error_msg,
                            **{k: v for k, v in payload.items() if k not in ("error", "file_path")},
                        },
                    )
                    final_return.extend(self.rollup_data([file], [error_data]))
                    continue

                doc_rows = payload.get("doc")
                if isinstance(doc_rows, list) and doc_rows:
                    # 结构化结果非空
                    rows: list[Data | None] = [
                        Data(
                            data={
                                "file_path": file_path,
                                **(item if isinstance(item, dict) else {"value": item}),
                            },
                        )
                        for item in doc_rows
                    ]
                    final_return.extend(self.rollup_data([file], rows))
                elif isinstance(doc_rows, list) and not doc_rows:
                    # 结构化结果为空：文件已处理但未提取到文本
                    self.log(f"No text extracted from '{file_path}', creating placeholder data")
                    empty_data = Data(
                        data={
                            "file_path": file_path,
                            "text": "(No text content extracted from image)",
                            "info": "Image processed successfully but contained no extractable text",
                            **{k: v for k, v in payload.items() if k != "doc"},
                        },
                    )
                    final_return.extend(self.rollup_data([file], [empty_data]))
                else:
                    # 非结构化结果保持原样（如 markdown 或错误字典）
                    # 确保 file_path 存在以便 rollup 匹配
                    if not payload.get("file_path"):
                        payload["file_path"] = file_path
                        # 补充 file_path 后重建 Data
                        advanced_data = Data(
                            data=payload,
                            text=getattr(advanced_data, "text", None),
                        )
                    final_return.extend(self.rollup_data([file], [advanced_data]))
            return final_return

        # 标准路径：多文件或未启用高级模式
        concurrency = 1 if not self.use_multithreading else max(1, self.concurrency_multithreading)

        file_paths = [str(f.path) for f in file_list]
        self.log(f"Starting parallel processing of {len(file_paths)} files with concurrency: {concurrency}.")
        my_data = parallel_load_data(
            file_paths,
            silent_errors=self.silent_errors,
            load_function=process_file_standard,
            max_concurrency=concurrency,
        )
        return self.rollup_data(file_list, my_data)

    # ------------------------------ 输出辅助 -----------------------------------

    def load_files_helper(self) -> DataFrame:
        result = self.load_files()

        # 结果为空则报错
        if result.empty:
            msg = "Could not extract content from the provided file(s)."
            raise ValueError(msg)

        # 若仅有错误列则直接抛错
        if "error" in result.columns:
            errors = result["error"].dropna().tolist()
            if errors and not any(col in result.columns for col in ["text", "doc", "exported_content"]):
                raise ValueError(errors[0])

        return result

    def load_files_dataframe(self) -> DataFrame:
        """使用 Docling 高级解析并返回 DataFrame。"""
        self.markdown = False
        return self.load_files_helper()

    def load_files_markdown(self) -> Message:
        """使用 Docling 高级解析并返回 Markdown。"""
        self.markdown = True
        result = self.load_files_helper()

        # 优先返回 text/exported_content
        if "text" in result.columns and not result["text"].isna().all():
            text_values = result["text"].dropna().tolist()
            if text_values:
                return Message(text=str(text_values[0]))

        if "exported_content" in result.columns and not result["exported_content"].isna().all():
            content_values = result["exported_content"].dropna().tolist()
            if content_values:
                return Message(text=str(content_values[0]))

        # 无文本时返回占位消息
        return Message(text="(No text content extracted from file)")
