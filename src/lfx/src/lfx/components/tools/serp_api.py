"""
模块名称：`SerpAPI` 搜索工具组件

本模块封装 SerpAPI 搜索能力，并对结果数量与摘要长度进行限制。
主要功能包括：
- 构建 SerpAPI 包装器并执行搜索
- 截断结果字段以控制上下文长度
- 提供结构化工具接口

关键组件：
- `SerpAPIComponent.build_tool`：构建搜索工具
- `SerpAPIComponent.run_model`：执行搜索并输出结果

设计背景：搜索结果过长会影响上下文预算，需要统一限制。
注意事项：依赖 SerpAPI 配额与网络可用性。
"""

from typing import Any

from langchain.tools import StructuredTool
from langchain_community.utilities.serpapi import SerpAPIWrapper
from langchain_core.tools import ToolException
from pydantic import BaseModel, Field

from lfx.base.langchain_utilities.model import LCToolComponent
from lfx.field_typing import Tool
from lfx.inputs.inputs import DictInput, IntInput, MultilineInput, SecretStrInput
from lfx.log.logger import logger
from lfx.schema.data import Data


class SerpAPISchema(BaseModel):
    """SerpAPI 搜索参数结构。"""

    query: str = Field(..., description="The search query")
    params: dict[str, Any] | None = Field(
        default={
            "engine": "google",
            "google_domain": "google.com",
            "gl": "us",
            "hl": "en",
        },
        description="Additional search parameters",
    )
    max_results: int = Field(5, description="Maximum number of results to return")
    max_snippet_length: int = Field(100, description="Maximum length of each result snippet")


class SerpAPIComponent(LCToolComponent):
    """SerpAPI 搜索工具组件。

    契约：输入查询与限制参数，输出截断后的结果列表。
    决策：在工具层完成摘要截断。
    问题：不受控的摘要长度会造成上下文膨胀。
    方案：统一裁剪 `title`/`snippet` 字段。
    代价：可能丢失细节信息。
    重评：当下游具备更细粒度控制时移除裁剪。
    """
    display_name = "Serp Search API"
    description = "Call Serp Search API with result limiting"
    name = "SerpAPI"
    icon = "SerpSearch"
    legacy = True
    replacement = ["serpapi.Serp"]

    inputs = [
        SecretStrInput(name="serpapi_api_key", display_name="SerpAPI API Key", required=True),
        MultilineInput(
            name="input_value",
            display_name="Input",
        ),
        DictInput(name="search_params", display_name="Parameters", advanced=True, is_list=True),
        IntInput(name="max_results", display_name="Max Results", value=5, advanced=True),
        IntInput(name="max_snippet_length", display_name="Max Snippet Length", value=100, advanced=True),
    ]

    def _build_wrapper(self, params: dict[str, Any] | None = None) -> SerpAPIWrapper:
        """构建 SerpAPI 包装器。"""
        params = params or {}
        if params:
            return SerpAPIWrapper(
                serpapi_api_key=self.serpapi_api_key,
                params=params,
            )
        return SerpAPIWrapper(serpapi_api_key=self.serpapi_api_key)

    def build_tool(self) -> Tool:
        """构建可调用的 SerpAPI 搜索工具。

        关键路径（三步）：
        1) 初始化或复用 wrapper
        2) 定义带截断与异常处理的搜索函数
        3) 构建结构化工具
        """
        wrapper = self._build_wrapper(self.search_params)

        def search_func(
            query: str, params: dict[str, Any] | None = None, max_results: int = 5, max_snippet_length: int = 100
        ) -> list[dict[str, Any]]:
            try:
                local_wrapper = wrapper
                if params:
                    local_wrapper = self._build_wrapper(params)

                full_results = local_wrapper.results(query)
                organic_results = full_results.get("organic_results", [])[:max_results]

                limited_results = []
                for result in organic_results:
                    limited_result = {
                        "title": result.get("title", "")[:max_snippet_length],
                        "link": result.get("link", ""),
                        "snippet": result.get("snippet", "")[:max_snippet_length],
                    }
                    limited_results.append(limited_result)

            except Exception as e:
                error_message = f"Error in SerpAPI search: {e!s}"
                logger.debug(error_message)
                raise ToolException(error_message) from e
            return limited_results

        tool = StructuredTool.from_function(
            name="serp_search_api",
            description="Search for recent results using SerpAPI with result limiting",
            func=search_func,
            args_schema=SerpAPISchema,
        )

        self.status = "SerpAPI Tool created"
        return tool

    def run_model(self) -> list[Data]:
        """执行搜索并返回结构化结果。"""
        tool = self.build_tool()
        try:
            results = tool.run(
                {
                    "query": self.input_value,
                    "params": self.search_params or {},
                    "max_results": self.max_results,
                    "max_snippet_length": self.max_snippet_length,
                }
            )

            data_list = [Data(data=result, text=result.get("snippet", "")) for result in results]

        except Exception as e:  # noqa: BLE001
            logger.debug("Error running SerpAPI", exc_info=True)
            self.status = f"Error: {e}"
            return [Data(data={"error": str(e)}, text=str(e))]

        self.status = data_list  # type: ignore[assignment]
        return data_list
