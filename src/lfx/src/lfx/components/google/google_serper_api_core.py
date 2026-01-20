"""
模块名称：`Google Serper API` 组件

本模块提供 `GoogleSerperAPICore`，用于调用 `Serper.dev` 搜索 API 并返回 `DataFrame`。
主要功能包括：
- 调用 `GoogleSerperAPIWrapper` 获取搜索结果
- 将结果整理为 `DataFrame`
- 提供文本化结果输出

关键组件：`GoogleSerperAPICore`
设计背景：为 `Serper` 搜索提供统一组件封装
注意事项：错误时返回带 `error` 字段的 `DataFrame`
"""

from langchain_community.utilities.google_serper import GoogleSerperAPIWrapper

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MultilineInput, Output, SecretStrInput
from lfx.schema.dataframe import DataFrame
from lfx.schema.message import Message


class GoogleSerperAPICore(Component):
    """`Google Serper API` 组件。
    契约：输入为 `Serper API Key` 与查询；输出为 `DataFrame` 或 `Message`。
    关键路径：构建 `wrapper` → 获取结果 → 结构化输出。
    决策：错误以 `DataFrame` 返回。问题：保证输出类型稳定；方案：错误行；代价：下游需处理错误字段；重评：当需要异常流时。
    """

    display_name = "Google Serper API"
    description = "Call the Serper.dev Google Search API."
    icon = "Serper"

    inputs = [
        SecretStrInput(
            name="serper_api_key",
            display_name="Serper API Key",
            required=True,
        ),
        MultilineInput(
            name="input_value",
            display_name="Input",
            tool_mode=True,
        ),
        IntInput(
            name="k",
            display_name="Number of results",
            value=4,
            required=True,
        ),
    ]

    outputs = [
        Output(
            display_name="Results",
            name="results",
            type_=DataFrame,
            method="search_serper",
        ),
    ]

    def search_serper(self) -> DataFrame:
        """执行搜索并返回 `DataFrame`。
        契约：成功返回结果；失败返回包含 `error` 的 `DataFrame`。
        关键路径：调用 `wrapper` → 提取 `organic` → 构建结果表。
        决策：仅返回 `organic` 结果。问题：避免噪声；方案：过滤；代价：丢失其他类型结果；重评：当需要更多字段时。
        """
        try:
            wrapper = self._build_wrapper()
            results = wrapper.results(query=self.input_value)
            list_results = results.get("organic", [])

            # 注意：将结果列表转换为 `DataFrame`。
            df_data = [
                {
                    "title": result.get("title", ""),
                    "link": result.get("link", ""),
                    "snippet": result.get("snippet", ""),
                }
                for result in list_results
            ]

            return DataFrame(df_data)
        except (ValueError, KeyError, ConnectionError) as e:
            error_message = f"Error occurred while searching: {e!s}"
            self.status = error_message
            # 注意：以错误行形式返回 `DataFrame`。
            return DataFrame([{"error": error_message}])

    def text_search_serper(self) -> Message:
        """返回文本化搜索结果。
        契约：返回 `Message`；无结果时返回提示文本。
        关键路径：调用 `search_serper` → 转换为字符串 → 返回。
        决策：使用 `DataFrame.to_string`。问题：便于展示；方案：文本化；代价：格式不稳定；重评：当需要结构化输出时。
        """
        search_results = self.search_serper()
        text_result = search_results.to_string(index=False) if not search_results.empty else "No results found."
        return Message(text=text_result)

    def _build_wrapper(self):
        """构建 `GoogleSerperAPIWrapper`。
        契约：返回 wrapper 实例。
        关键路径：透传 `serper_api_key` 与 `k`。
        决策：不在此处校验 key。问题：校验需远端调用；方案：执行时失败；代价：晚失败；重评：当需要前置校验时。
        """
        return GoogleSerperAPIWrapper(serper_api_key=self.serper_api_key, k=self.k)

    def build(self):
        """返回可调用的搜索函数。
        契约：返回 `search_serper`。
        关键路径：直接返回方法引用。
        决策：以 `build` 暴露执行入口。问题：符合组件约定；方案：返回方法；代价：无额外控制；重评：当需要异步入口时。
        """
        return self.search_serper
