"""
模块名称：lfx.components.unstructured.unstructured

本模块提供基于 Unstructured.io API 的文件解析能力，主要用于将多类型文件转为结构化文本数据。主要功能包括：
- 功能1：按 `VALID_EXTENSIONS` 过滤支持文件类型
- 功能2：组装 API 参数并调用 Unstructured Loader
- 功能3：将返回文档转换为 `Data` 并规范字段

关键组件：
- UnstructuredComponent：封装文件处理流程与参数契约

设计背景：统一文件解析入口，避免各组件重复对接外部 API。
注意事项：必须提供 `api_key`；可选 `api_url` 与 `chunking_strategy` 会影响输出；返回数据会重命名 `source` 字段。
"""

from langchain_unstructured import UnstructuredLoader

from lfx.base.data.base_file import BaseFileComponent
from lfx.inputs.inputs import DropdownInput, MessageTextInput, NestedDictInput, SecretStrInput
from lfx.schema.data import Data


class UnstructuredComponent(BaseFileComponent):
    display_name = "Unstructured API"
    description = (
        "Uses Unstructured.io API to extract clean text from raw source documents. Supports a wide range of file types."
    )
    documentation = (
        "https://python.langchain.com/api_reference/unstructured/document_loaders/"
        "langchain_unstructured.document_loaders.UnstructuredLoader.html"
    )
    trace_type = "tool"
    icon = "Unstructured"
    name = "Unstructured"

    VALID_EXTENSIONS = [
        "bmp",
        "csv",
        "doc",
        "docx",
        "eml",
        "epub",
        "heic",
        "html",
        "jpeg",
        "png",
        "md",
        "msg",
        "odt",
        "org",
        "p7s",
        "pdf",
        "png",
        "ppt",
        "pptx",
        "rst",
        "rtf",
        "tiff",
        "txt",
        "tsv",
        "xls",
        "xlsx",
        "xml",
    ]

    inputs = [
        *BaseFileComponent.get_base_inputs(),
        SecretStrInput(
            name="api_key",
            display_name="Unstructured.io Serverless API Key",
            required=True,
            info="Unstructured API Key. Create at: https://app.unstructured.io/",
        ),
        MessageTextInput(
            name="api_url",
            display_name="Unstructured.io API URL",
            required=False,
            info="Unstructured API URL.",
        ),
        DropdownInput(
            name="chunking_strategy",
            display_name="Chunking Strategy",
            info="Chunking strategy to use, see https://docs.unstructured.io/api-reference/api-services/chunking",
            options=["", "basic", "by_title", "by_page", "by_similarity"],
            real_time_refresh=False,
            value="",
        ),
        NestedDictInput(
            name="unstructured_args",
            display_name="Additional Arguments",
            required=False,
            info=(
                "Optional dictionary of additional arguments to the Loader. "
                "See https://docs.unstructured.io/api-reference/api-services/api-parameters for more information."
            ),
        ),
    ]

    outputs = [
        *BaseFileComponent.get_base_outputs(),
    ]

    def process_files(self, file_list: list[BaseFileComponent.BaseFile]) -> list[BaseFileComponent.BaseFile]:
        file_paths = [str(file.path) for file in file_list if file.path]

        if not file_paths:
            self.log("No files to process.")
            return file_list

        # 注意：`unstructured_args` 透传 API 参数，错误键值会由 API 直接报错
        args = self.unstructured_args or {}

        if self.chunking_strategy:
            args["chunking_strategy"] = self.chunking_strategy

        # 注意：强制使用 API 端分区（`partition_via_api=True`）
        args["api_key"] = self.api_key
        args["partition_via_api"] = True
        if self.api_url:
            args["url"] = self.api_url

        loader = UnstructuredLoader(
            file_paths,
            **args,
        )

        documents = loader.load()

        processed_data: list[Data | None] = [Data.from_document(doc) if doc else None for doc in documents]

        # 注意：重命名 `source` 字段以避免与内部字段冲突
        for data in processed_data:
            if data and "source" in data.data:
                data.data[self.SERVER_FILE_PATH_FIELDNAME] = data.data.pop("source")

        return self.rollup_data(file_list, processed_data)
