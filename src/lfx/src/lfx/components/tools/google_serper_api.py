"""
模块名称：`Google Serper` 搜索工具组件

本模块封装 Serper.dev 的 Google 搜索接口，并提供结构化结果输出。
主要功能包括：
- 根据查询类型选择不同结果集合
- 支持附加查询参数
- 生成 LangChain 结构化工具

关键组件：
- `GoogleSerperAPIComponent.run_model`：执行查询并结构化返回
- `GoogleSerperAPIComponent._build_wrapper`：构建 API 包装器

设计背景：为低代码流程提供替代 Google 搜索的稳定入口（已标记为弃用）。
注意事项：需要 `serper_api_key`，且结果字段会按类型变化。
"""

from typing import Any

from langchain.tools import StructuredTool
from langchain_community.utilities.google_serper import GoogleSerperAPIWrapper
from pydantic import BaseModel, Field

from lfx.base.langchain_utilities.model import LCToolComponent
from lfx.field_typing import Tool
from lfx.inputs.inputs import (
    DictInput,
    DropdownInput,
    IntInput,
    MultilineInput,
    SecretStrInput,
)
from lfx.schema.data import Data


class QuerySchema(BaseModel):
    """Serper 查询参数结构。"""
    query: str = Field(..., description="The query to search for.")
    query_type: str = Field(
        "search",
        description="The type of search to perform (e.g., 'news' or 'search').",
    )
    k: int = Field(4, description="The number of results to return.")
    query_params: dict[str, Any] = Field({}, description="Additional query parameters to pass to the API.")


class GoogleSerperAPIComponent(LCToolComponent):
    """Serper.dev 搜索工具组件（弃用）。

    契约：输入查询与类型，输出搜索结果 `Data` 列表。
    决策：依据 `query_type` 从不同字段提取结果。
    问题：不同搜索类型返回结构不一致。
    方案：将 `search/news` 显式分支并标准化字段。
    代价：新增类型需扩展分支逻辑。
    重评：当 API 结构稳定或统一时简化解析。
    """
    display_name = "Google Serper API [DEPRECATED]"
    description = "Call the Serper.dev Google Search API."
    name = "GoogleSerperAPI"
    icon = "Google"
    legacy = True
    inputs = [
        SecretStrInput(name="serper_api_key", display_name="Serper API Key", required=True),
        MultilineInput(
            name="query",
            display_name="Query",
        ),
        IntInput(name="k", display_name="Number of results", value=4, required=True),
        DropdownInput(
            name="query_type",
            display_name="Query Type",
            required=False,
            options=["news", "search"],
            value="search",
        ),
        DictInput(
            name="query_params",
            display_name="Query Params",
            required=False,
            value={
                "gl": "us",
                "hl": "en",
            },
            list=True,
        ),
    ]

    def run_model(self) -> Data | list[Data]:
        """执行 Serper 搜索并返回结构化结果。"""
        wrapper = self._build_wrapper(self.k, self.query_type, self.query_params)
        results = wrapper.results(query=self.query)

        # 注意：根据 `query_type` 选择对应结果集合。
        if self.query_type == "search":
            list_results = results.get("organic", [])
        elif self.query_type == "news":
            list_results = results.get("news", [])
        else:
            list_results = []

        data_list = []
        for result in list_results:
            result["text"] = result.pop("snippet", "")
            data_list.append(Data(data=result))
        self.status = data_list
        return data_list

    def build_tool(self) -> Tool:
        """构建可被 LangChain 调用的结构化工具。"""
        return StructuredTool.from_function(
            name="google_search",
            description="Search Google for recent results.",
            func=self._search,
            args_schema=self.QuerySchema,
        )

    def _build_wrapper(
        self,
        k: int = 5,
        query_type: str = "search",
        query_params: dict | None = None,
    ) -> GoogleSerperAPIWrapper:
        """初始化 Serper API 包装器并合并参数。"""
        wrapper_args = {
            "serper_api_key": self.serper_api_key,
            "k": k,
            "type": query_type,
        }

        # 实现：将额外参数合并到 wrapper 参数。
        if query_params:
            wrapper_args.update(query_params)  # 注意：合并附加查询参数。

        # 实现：动态传参初始化 wrapper。
        return GoogleSerperAPIWrapper(**wrapper_args)

    def _search(
        self,
        query: str,
        k: int = 5,
        query_type: str = "search",
        query_params: dict | None = None,
    ) -> dict:
        """执行搜索并返回原始结果。"""
        wrapper = self._build_wrapper(k, query_type, query_params)
        return wrapper.results(query=query)
