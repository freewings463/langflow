"""
模块名称：`Wikidata` 搜索工具组件

本模块封装 Wikidata 搜索 API，提供相似度查询与结构化输出。
主要功能包括：
- 构建查询参数并请求 Wikidata API
- 对搜索结果进行异常处理
- 将结果转换为 `Data`

关键组件：
- `WikidataAPIWrapper.results`：请求并返回原始结果
- `WikidataAPIComponent.run_model`：输出结构化结果

设计背景：为知识检索场景提供轻量的实体搜索能力。
注意事项：网络异常或无结果会抛 `ToolException`。
"""

from typing import Any

import httpx
from langchain_core.tools import StructuredTool, ToolException
from pydantic import BaseModel, Field

from lfx.base.langchain_utilities.model import LCToolComponent
from lfx.field_typing import Tool
from lfx.inputs.inputs import MultilineInput
from lfx.schema.data import Data


class WikidataSearchSchema(BaseModel):
    """Wikidata 搜索参数结构。"""
    query: str = Field(..., description="The search query for Wikidata")


class WikidataAPIWrapper(BaseModel):
    """Wikidata API 简单包装器。"""

    wikidata_api_url: str = "https://www.wikidata.org/w/api.php"

    def results(self, query: str) -> list[dict[str, Any]]:
        """执行搜索并返回原始结果。"""
        # 实现：构造 Wikidata 查询参数。
        params = {
            "action": "wbsearchentities",
            "format": "json",
            "search": query,
            "language": "en",
        }

        # 实现：发送请求并解析响应。
        response = httpx.get(self.wikidata_api_url, params=params)
        response.raise_for_status()
        response_json = response.json()

        # 实现：提取搜索结果字段。
        return response_json.get("search", [])

    def run(self, query: str) -> list[dict[str, Any]]:
        """执行查询并将错误包装为 `ToolException`。"""
        try:
            results = self.results(query)
            if results:
                return results

            error_message = "No search results found for the given query."

            raise ToolException(error_message)

        except Exception as e:
            error_message = f"Error in Wikidata Search API: {e!s}"

            raise ToolException(error_message) from e


class WikidataAPIComponent(LCToolComponent):
    """Wikidata 搜索组件。

    契约：输入查询文本，输出 `Data` 列表。
    决策：使用轻量 wrapper 直接调用 API。
    问题：重复封装请求逻辑会增加维护成本。
    方案：集中在 wrapper 中处理请求与异常。
    代价：缺少复杂的缓存与重试策略。
    重评：当调用频率升高时引入缓存或重试。
    """
    display_name = "Wikidata API"
    description = "Performs a search using the Wikidata API."
    name = "WikidataAPI"
    icon = "Wikipedia"
    legacy = True
    replacement = ["wikipedia.WikidataComponent"]

    inputs = [
        MultilineInput(
            name="query",
            display_name="Query",
            info="The text query for similarity search on Wikidata.",
            required=True,
        ),
    ]

    def build_tool(self) -> Tool:
        """构建可调用的 Wikidata 搜索工具。"""
        wrapper = WikidataAPIWrapper()

        # 实现：将 wrapper.run 作为工具函数。
        tool = StructuredTool.from_function(
            name="wikidata_search_api",
            description="Perform similarity search on Wikidata API",
            func=wrapper.run,
            args_schema=WikidataSearchSchema,
        )

        self.status = "Wikidata Search API Tool for Langchain"

        return tool

    def run_model(self) -> list[Data]:
        """执行查询并转换为 `Data` 列表。"""
        tool = self.build_tool()

        results = tool.run({"query": self.query})

        # 实现：将 API 响应映射为 `Data`。
        data = [
            Data(
                text=result["label"],
                metadata=result,
            )
            for result in results
        ]

        self.status = data  # type: ignore[assignment]

        return data
