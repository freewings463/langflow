"""
模块名称：Exa 搜索工具包组件

本模块提供 Exa 搜索的工具包组件封装，主要用于将 Exa（Metaphor）API
作为可调用工具暴露给 LFX 组件体系。
主要功能包括：
- 构建 Exa API 客户端并封装为工具函数
- 提供搜索、内容抓取与相似搜索能力

关键组件：
- `ExaSearchToolkit`：Exa 搜索工具包组件

设计背景：在 LFX 中以统一工具接口接入 Exa 搜索能力。
注意事项：依赖 `metaphor_python`，缺失会导致运行时导入错误。
"""

from langchain_core.tools import tool
from metaphor_python import Metaphor

from lfx.custom.custom_component.component import Component
from lfx.field_typing import Tool
from lfx.io import BoolInput, IntInput, Output, SecretStrInput


class ExaSearchToolkit(Component):
    """Exa 搜索工具包组件。

    契约：`build_toolkit()` 返回包含 `search/get_contents/find_similar` 的工具列表。
    副作用：调用 Exa API，依赖外部网络与 API key。
    失败语义：API 调用异常将由下游工具执行时抛出。
    """

    display_name = "Exa Search"
    description = "Exa Search toolkit for search and content retrieval"
    documentation = "https://python.langchain.com/docs/integrations/tools/metaphor_search"
    beta = True
    name = "ExaSearch"
    icon = "ExaSearch"

    inputs = [
        SecretStrInput(
            name="metaphor_api_key",
            display_name="Exa Search API Key",
            password=True,
        ),
        BoolInput(
            name="use_autoprompt",
            display_name="Use Autoprompt",
            value=True,
        ),
        IntInput(
            name="search_num_results",
            display_name="Search Number of Results",
            value=5,
        ),
        IntInput(
            name="similar_num_results",
            display_name="Similar Number of Results",
            value=5,
        ),
    ]

    outputs = [
        Output(name="tools", display_name="Tools", method="build_toolkit"),
    ]

    def build_toolkit(self) -> Tool:
        """构建 Exa 搜索相关工具集合。

        关键路径（三步）：
        1) 初始化 Exa API 客户端
        2) 定义搜索与内容相关工具函数
        3) 返回工具列表供上层注册
        异常流：客户端初始化或网络异常在工具调用时抛出。
        排障入口：检查 API key 与请求参数。
        """
        client = Metaphor(api_key=self.metaphor_api_key)

        @tool
        def search(query: str):
            """执行搜索请求并返回结果列表。"""
            return client.search(query, use_autoprompt=self.use_autoprompt, num_results=self.search_num_results)

        @tool
        def get_contents(ids: list[str]):
            """根据搜索结果 ID 获取网页内容。"""
            return client.get_contents(ids)

        @tool
        def find_similar(url: str):
            """根据 URL 获取相似结果。"""
            return client.find_similar(url, num_results=self.similar_num_results)

        return [search, get_contents, find_similar]
