"""
模块名称：duck_duck_go_search_run

本模块提供 DuckDuckGo 搜索组件，封装搜索请求并输出结构化结果。
主要功能包括：
- 功能1：执行 DuckDuckGo 搜索并返回结果列表。
- 功能2：将搜索结果包装为 `Data`/`DataFrame`。

使用场景：在 Langflow 流程或工具中执行网页搜索。
关键组件：
- 类 `DuckDuckGoSearchComponent`

设计背景：复用 LangChain DuckDuckGo 工具并统一输出格式。
注意事项：搜索结果为文本拼接，需要按行拆分；结果长度可能受网络与服务限制。
"""

from langchain_community.tools import DuckDuckGoSearchRun

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import IntInput, MessageTextInput
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame
from lfx.template.field.base import Output


class DuckDuckGoSearchComponent(Component):
    """DuckDuckGo 搜索组件。

    契约：输入为搜索词与结果/片段限制；输出为 `DataFrame`。
    关键路径：
    1) 构建 DuckDuckGo 工具；
    2) 执行搜索并拆分结果；
    3) 生成 `Data` 列表与 `DataFrame`。
    异常流：工具调用失败返回错误 `Data` 并更新状态。
    排障入口：`self.status` 中包含错误信息或结果摘要。
    决策：
    问题：搜索结果原始格式为多行文本，不便于下游结构化处理。
    方案：逐行拆分并生成 `Data`，同时提供 `DataFrame` 输出。
    代价：丢失原始结果结构与部分元信息。
    重评：当工具返回结构化结果或支持 JSON 输出时。
    """

    display_name = "DuckDuckGo Search"
    description = "Search the web using DuckDuckGo with customizable result limits"
    documentation = "https://python.langchain.com/docs/integrations/tools/ddg"
    icon = "DuckDuckGo"

    inputs = [
        MessageTextInput(
            name="input_value",
            display_name="Search Query",
            required=True,
            info="The search query to execute with DuckDuckGo",
            tool_mode=True,
        ),
        IntInput(
            name="max_results",
            display_name="Max Results",
            value=5,
            required=False,
            advanced=True,
            info="Maximum number of search results to return",
        ),
        IntInput(
            name="max_snippet_length",
            display_name="Max Snippet Length",
            value=100,
            required=False,
            advanced=True,
            info="Maximum length of each result snippet",
        ),
    ]

    outputs = [
        Output(display_name="DataFrame", name="dataframe", method="fetch_content_dataframe"),
    ]

    def _build_wrapper(self) -> DuckDuckGoSearchRun:
        """构建 DuckDuckGo 搜索工具实例。

        契约：返回 `DuckDuckGoSearchRun` 实例。
        关键路径：直接实例化工具对象。
        决策：
        问题：搜索工具可能需要统一封装以便替换实现。
        方案：单独封装为私有方法，便于后续扩展。
        代价：无。
        重评：当需要注入代理或自定义参数时。
        """
        return DuckDuckGoSearchRun()

    def run_model(self) -> DataFrame:
        """组件默认执行入口，返回搜索结果 DataFrame。

        契约：与 `fetch_content_dataframe` 一致。
        关键路径：直接转发到 `fetch_content_dataframe`。
        决策：
        问题：组件输出需要统一为 DataFrame。
        方案：默认执行 DataFrame 输出。
        代价：无法直接返回原始 `Data` 列表。
        重评：当需要多输出类型时。
        """
        return self.fetch_content_dataframe()

    def fetch_content(self) -> list[Data]:
        """执行搜索并返回 `Data` 列表。

        契约：返回最多 `max_results` 条；每条包含 `content` 与 `snippet`。
        关键路径：调用 `wrapper.run` -> 按行拆分 -> 截断片段 -> 构建 `Data`。
        异常流：参数异常或工具错误返回包含错误信息的 `Data`。
        决策：
        问题：搜索结果为多行文本，需要统一为结构化对象。
        方案：按行拆分并截断片段，保留原始内容。
        代价：结果行格式依赖工具输出稳定性。
        重评：当工具返回结构化结果或新增分页时。
        """
        try:
            wrapper = self._build_wrapper()

            full_results = wrapper.run(f"{self.input_value} (site:*)")

            result_list = full_results.split("\n")[: self.max_results]

            data_results = []
            for result in result_list:
                if result.strip():
                    snippet = result[: self.max_snippet_length]
                    data_results.append(
                        Data(
                            text=snippet,
                            data={
                                "content": result,
                                "snippet": snippet,
                            },
                        )
                    )
        except (ValueError, AttributeError) as e:
            error_data = [Data(text=str(e), data={"error": str(e)})]
            self.status = error_data
            return error_data
        else:
            self.status = data_results
            return data_results

    def fetch_content_dataframe(self) -> DataFrame:
        """将搜索结果转换为 `DataFrame`。

        契约：`DataFrame` 内容来源于 `fetch_content`。
        关键路径：获取 `Data` 列表 -> 包装为 `DataFrame`。
        决策：
        问题：下游通常需要表格格式进行过滤与展示。
        方案：统一输出 `DataFrame`。
        代价：保留的信息与 `Data` 一致，无额外字段。
        重评：当需要额外列或结构化元信息时。
        """
        data = self.fetch_content()
        return DataFrame(data)
