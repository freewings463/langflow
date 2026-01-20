"""
模块名称：`SearchAPI` 搜索工具组件

本模块封装 searchapi.io 的搜索能力，并对结果数量与摘要长度进行限制。
主要功能包括：
- 构建 API 包装器并执行搜索
- 截断结果标题与摘要以控制输出大小
- 生成结构化工具用于链式调用

关键组件：
- `SearchAPIComponent.build_tool`：构建搜索工具
- `SearchAPIComponent.run_model`：执行搜索并返回 `Data`

设计背景：搜索结果过长会影响上下文窗口，需要统一限制输出。
注意事项：依赖 `searchapi.io` 的接口与配额限制。
"""

from typing import Any

from langchain.tools import StructuredTool
from langchain_community.utilities.searchapi import SearchApiAPIWrapper
from pydantic import BaseModel, Field

from lfx.base.langchain_utilities.model import LCToolComponent
from lfx.field_typing import Tool
from lfx.inputs.inputs import DictInput, IntInput, MessageTextInput, MultilineInput, SecretStrInput
from lfx.schema.data import Data


class SearchAPIComponent(LCToolComponent):
    """searchapi.io 搜索工具组件。

    契约：输入查询与限制参数，输出截断后的结果列表。
    决策：在工具层完成摘要截断而非交给下游处理。
    问题：未经限制的摘要会占用上下文预算。
    方案：在返回前统一裁剪 `title`/`snippet`。
    代价：可能丢失细节信息。
    重评：当下游已具备摘要控制能力时移除裁剪。
    """
    display_name: str = "Search API"
    description: str = "Call the searchapi.io API with result limiting"
    name = "SearchAPI"
    documentation: str = "https://www.searchapi.io/docs/google"
    icon = "SearchAPI"
    legacy = True
    replacement = ["searchapi.SearchComponent"]

    inputs = [
        MessageTextInput(name="engine", display_name="Engine", value="google"),
        SecretStrInput(name="api_key", display_name="SearchAPI API Key", required=True),
        MultilineInput(
            name="input_value",
            display_name="Input",
        ),
        DictInput(name="search_params", display_name="Search parameters", advanced=True, is_list=True),
        IntInput(name="max_results", display_name="Max Results", value=5, advanced=True),
        IntInput(name="max_snippet_length", display_name="Max Snippet Length", value=100, advanced=True),
    ]

    class SearchAPISchema(BaseModel):
        """搜索参数结构定义。"""
        query: str = Field(..., description="The search query")
        params: dict[str, Any] = Field(default_factory=dict, description="Additional search parameters")
        max_results: int = Field(5, description="Maximum number of results to return")
        max_snippet_length: int = Field(100, description="Maximum length of each result snippet")

    def _build_wrapper(self):
        """构建 searchapi.io 包装器。"""
        return SearchApiAPIWrapper(engine=self.engine, searchapi_api_key=self.api_key)

    def build_tool(self) -> Tool:
        """构建可调用的搜索工具。

        关键路径（三步）：
        1) 初始化 API wrapper
        2) 定义带截断逻辑的搜索函数
        3) 构建结构化工具
        """
        wrapper = self._build_wrapper()

        def search_func(
            query: str, params: dict[str, Any] | None = None, max_results: int = 5, max_snippet_length: int = 100
        ) -> list[dict[str, Any]]:
            params = params or {}
            full_results = wrapper.results(query=query, **params)
            organic_results = full_results.get("organic_results", [])[:max_results]

            limited_results = []
            for result in organic_results:
                limited_result = {
                    "title": result.get("title", "")[:max_snippet_length],
                    "link": result.get("link", ""),
                    "snippet": result.get("snippet", "")[:max_snippet_length],
                }
                limited_results.append(limited_result)

            return limited_results

        tool = StructuredTool.from_function(
            name="search_api",
            description="Search for recent results using searchapi.io with result limiting",
            func=search_func,
            args_schema=self.SearchAPISchema,
        )

        self.status = f"Search API Tool created with engine: {self.engine}"
        return tool

    def run_model(self) -> list[Data]:
        """执行搜索并返回结构化结果。"""
        tool = self.build_tool()
        results = tool.run(
            {
                "query": self.input_value,
                "params": self.search_params or {},
                "max_results": self.max_results,
                "max_snippet_length": self.max_snippet_length,
            }
        )

        data_list = [Data(data=result, text=result.get("snippet", "")) for result in results]

        self.status = data_list
        return data_list
