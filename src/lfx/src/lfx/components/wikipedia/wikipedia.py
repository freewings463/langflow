"""
模块名称：Wikipedia 查询组件

本模块提供 Wikipedia API 的封装，主要用于根据关键词检索并返回页面摘要。主要功能包括：
- 构建 `WikipediaAPIWrapper` 并配置查询参数
- 调用 Wikipedia API 获取文档列表
- 将文档转换为 `Data`/`DataFrame` 返回

关键组件：
- `WikipediaComponent`：组件主体
- `_build_wrapper`：构建 Wikipedia API 包装器
- `fetch_content`：获取文档并转换为 `Data`

设计背景：复用 LangChain 的 Wikipedia 适配器，统一数据结构输出。
使用场景：为流程提供百科类信息检索能力。
注意事项：查询结果受 `k` 与 `doc_content_chars_max` 限制；网络错误由底层抛出。
"""

from langchain_community.utilities.wikipedia import WikipediaAPIWrapper

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import BoolInput, IntInput, MessageTextInput, MultilineInput
from lfx.io import Output
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame


class WikipediaComponent(Component):
    """Wikipedia 查询组件。

    契约：输入 `input_value`/`lang`/`k` 等；输出 `DataFrame`。
    副作用：发起网络请求并更新 `self.status`。
    失败语义：底层 API 异常会向上抛出。
    关键路径：1) 构建 wrapper 2) 调用 load 3) 转换为 `Data`/`DataFrame`。
    决策：使用 `WikipediaAPIWrapper` 而非手写请求。
    问题：需要稳定的百科检索接口与统一数据结构。
    方案：复用 LangChain 社区封装。
    代价：受封装层行为与版本变化影响。
    重评：当需要更细粒度控制或缓存时替换实现。
    """

    display_name = "Wikipedia"
    description = "Call Wikipedia API."
    icon = "Wikipedia"

    inputs = [
        MultilineInput(
            name="input_value",
            display_name="Input",
            tool_mode=True,
        ),
        MessageTextInput(name="lang", display_name="Language", value="en"),
        IntInput(name="k", display_name="Number of results", value=4, required=True),
        BoolInput(name="load_all_available_meta", display_name="Load all available meta", value=False, advanced=True),
        IntInput(
            name="doc_content_chars_max", display_name="Document content characters max", value=4000, advanced=True
        ),
    ]

    outputs = [
        Output(display_name="DataFrame", name="dataframe", method="fetch_content_dataframe"),
    ]

    def run_model(self) -> DataFrame:
        """运行组件主逻辑并返回 `DataFrame`。"""
        return self.fetch_content_dataframe()

    def _build_wrapper(self) -> WikipediaAPIWrapper:
        """构建并返回 Wikipedia API 包装器。

        契约：使用组件输入配置语言、结果数量与内容长度。
        副作用：无。
        """
        return WikipediaAPIWrapper(
            top_k_results=self.k,
            lang=self.lang,
            load_all_available_meta=self.load_all_available_meta,
            doc_content_chars_max=self.doc_content_chars_max,
        )

    def fetch_content(self) -> list[Data]:
        """调用 Wikipedia API 并返回 `Data` 列表。

        契约：`input_value` 作为查询词；每条 `Data` 来自 LangChain `Document`。
        副作用：发起网络请求并更新 `self.status`。
        失败语义：底层调用异常原样上抛。
        """
        wrapper = self._build_wrapper()
        docs = wrapper.load(self.input_value)
        data = [Data.from_document(doc) for doc in docs]
        self.status = data
        return data

    def fetch_content_dataframe(self) -> DataFrame:
        """将查询结果转换为 `DataFrame` 返回。"""
        data = self.fetch_content()
        return DataFrame(data)
