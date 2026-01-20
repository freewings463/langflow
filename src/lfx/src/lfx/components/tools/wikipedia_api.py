"""
模块名称：`Wikipedia` 搜索工具组件

本模块封装 Wikipedia API 的检索能力，并将文档结果转换为 `Data`。
主要功能包括：
- 构建 Wikipedia API 包装器
- 执行查询并加载文档
- 提供 LangChain 工具接口

关键组件：
- `WikipediaAPIComponent.run_model`：执行查询并返回结果
- `WikipediaAPIComponent._build_wrapper`：配置 API 参数

设计背景：为知识检索场景提供标准化 Wikipedia 入口。
注意事项：返回文档长度受 `doc_content_chars_max` 限制。
"""

from typing import cast

from langchain_community.tools import WikipediaQueryRun
from langchain_community.utilities.wikipedia import WikipediaAPIWrapper

from lfx.base.langchain_utilities.model import LCToolComponent
from lfx.field_typing import Tool
from lfx.inputs.inputs import BoolInput, IntInput, MessageTextInput, MultilineInput
from lfx.schema.data import Data


class WikipediaAPIComponent(LCToolComponent):
    """Wikipedia 搜索组件。

    契约：输入查询文本与参数，输出 `Data` 列表。
    决策：使用 `WikipediaAPIWrapper` 统一查询逻辑。
    问题：直接拼装请求参数容易与上游工具不一致。
    方案：通过 wrapper 统一参数管理。
    代价：依赖第三方库更新。
    重评：当 API 发生变化或官方接口升级时调整 wrapper。
    """
    display_name = "Wikipedia API"
    description = "Call Wikipedia API."
    name = "WikipediaAPI"
    icon = "Wikipedia"
    legacy = True
    replacement = ["wikipedia.WikipediaComponent"]

    inputs = [
        MultilineInput(
            name="input_value",
            display_name="Input",
        ),
        MessageTextInput(name="lang", display_name="Language", value="en"),
        IntInput(name="k", display_name="Number of results", value=4, required=True),
        BoolInput(name="load_all_available_meta", display_name="Load all available meta", value=False, advanced=True),
        IntInput(
            name="doc_content_chars_max", display_name="Document content characters max", value=4000, advanced=True
        ),
    ]

    def run_model(self) -> list[Data]:
        """执行检索并返回结构化结果。"""
        wrapper = self._build_wrapper()
        docs = wrapper.load(self.input_value)
        data = [Data.from_document(doc) for doc in docs]
        self.status = data
        return data

    def build_tool(self) -> Tool:
        """构建可调用的 Wikipedia 工具。"""
        wrapper = self._build_wrapper()
        return cast("Tool", WikipediaQueryRun(api_wrapper=wrapper))

    def _build_wrapper(self) -> WikipediaAPIWrapper:
        """构建 Wikipedia API 包装器。"""
        return WikipediaAPIWrapper(
            top_k_results=self.k,
            lang=self.lang,
            load_all_available_meta=self.load_all_available_meta,
            doc_content_chars_max=self.doc_content_chars_max,
        )
