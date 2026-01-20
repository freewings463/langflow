"""
模块名称：SearchAPI 搜索组件

模块目的：封装 SearchApi 的搜索能力并输出 `Data`/`DataFrame` 结构。
使用场景：在流程中调用外部搜索引擎（Google/Bing/DuckDuckGo）获取结果。
主要功能包括：
- 定义搜索输入参数（引擎、查询、参数、结果截断）
- 调用 `SearchApiAPIWrapper` 获取搜索结果
- 将结果转换为 `Data` 列表与 `DataFrame`

关键组件：
- `SearchComponent`：搜索组件入口

设计背景：复用 LangChain 社区封装，减少 API 适配成本。
注意：`api_key` 缺失或无效会导致调用失败，调用方需提示配置问题。
"""

from typing import Any

from langchain_community.utilities.searchapi import SearchApiAPIWrapper

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import DictInput, DropdownInput, IntInput, MultilineInput, SecretStrInput
from lfx.io import Output
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame


class SearchComponent(Component):
    """SearchAPI 搜索组件。

    契约：输入搜索参数与查询文本，输出结构化结果 `DataFrame`。
    关键路径：由 `fetch_content` 拉取结果并裁剪字段，再由 `fetch_content_dataframe` 包装。

    决策：通过 `SearchApiAPIWrapper` 适配 SearchApi
    问题：需要统一不同搜索引擎的结果结构
    方案：复用上游 wrapper 并自行裁剪字段
    代价：依赖上游返回结构（如 `organic_results`）稳定
    重评：当返回结构变更或需要更多字段时
    """
    display_name: str = "SearchApi"
    description: str = "Calls the SearchApi API with result limiting. Supports Google, Bing and DuckDuckGo."
    documentation: str = "https://www.searchapi.io/docs/google"
    icon = "SearchAPI"

    inputs = [
        DropdownInput(name="engine", display_name="Engine", value="google", options=["google", "bing", "duckduckgo"]),
        SecretStrInput(name="api_key", display_name="SearchAPI API Key", required=True),
        MultilineInput(
            name="input_value",
            display_name="Input",
            tool_mode=True,
        ),
        DictInput(name="search_params", display_name="Search parameters", advanced=True, is_list=True),
        IntInput(name="max_results", display_name="Max Results", value=5, advanced=True),
        IntInput(name="max_snippet_length", display_name="Max Snippet Length", value=100, advanced=True),
    ]

    outputs = [
        Output(display_name="DataFrame", name="dataframe", method="fetch_content_dataframe"),
    ]

    def _build_wrapper(self):
        """构建 SearchApi 客户端包装器。

        契约：依赖 `engine` 与 `api_key`，返回可执行搜索请求的 wrapper。
        失败语义：缺失或无效 `api_key` 将在调用阶段触发异常。
        """
        return SearchApiAPIWrapper(engine=self.engine, searchapi_api_key=self.api_key)

    def run_model(self) -> DataFrame:
        """组件运行入口，保持与框架 `run_model` 约定一致。"""
        return self.fetch_content_dataframe()

    def fetch_content(self) -> list[Data]:
        """执行搜索并返回结构化结果列表。

        契约：返回 `Data` 列表，字段包含 `title`/`link`/`snippet`（已截断）。
        副作用：调用外部 SearchApi 服务（网络 I/O）。

        关键路径（三步）：
        1) 创建 wrapper
        2) 执行搜索并截断 `organic_results`
        3) 转换为 `Data` 并写入 `status`

        注意：依赖 `organic_results` 字段存在；缺失时返回空列表。
        性能：远端搜索耗时，结果量受 `max_results` 限制。
        排障：关注上游异常堆栈与 SearchApi 返回错误信息。
        """
        wrapper = self._build_wrapper()

        def search_func(
            query: str, params: dict[str, Any] | None = None, max_results: int = 5, max_snippet_length: int = 100
        ) -> list[Data]:
            params = params or {}
            full_results = wrapper.results(query=query, **params)
            organic_results = full_results.get("organic_results", [])[:max_results]

            return [
                Data(
                    text=result.get("snippet", ""),
                    data={
                        "title": result.get("title", "")[:max_snippet_length],
                        "link": result.get("link", ""),
                        "snippet": result.get("snippet", "")[:max_snippet_length],
                    },
                )
                for result in organic_results
            ]

        results = search_func(
            self.input_value,
            self.search_params or {},
            self.max_results,
            self.max_snippet_length,
        )
        self.status = results
        return results

    def fetch_content_dataframe(self) -> DataFrame:
        """将搜索结果转换为 `DataFrame` 以便下游消费。

        契约：返回包含搜索结果的 `DataFrame`。
        失败语义：若上游失败将由 `fetch_content` 抛异常。
        """
        data = self.fetch_content()
        return DataFrame(data)
