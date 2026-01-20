"""
模块名称：`Bing Search API` 组件

本模块提供基于 `Bing Search API` 的搜索组件，主要用于拉取检索结果并输出为数据表或工具。
主要功能包括：
- 执行搜索并返回 `Data` 列表
- 将结果封装为 `DataFrame` 输出
- 构建 `LangChain` 工具供代理调用

关键组件：
- `BingSearchAPIComponent`

设计背景：为 LangFlow 提供统一的 Bing 搜索能力入口。
注意事项：需提供有效的订阅密钥与可选的自定义搜索 URL。
"""

from typing import cast

from langchain_community.tools.bing_search import BingSearchResults
from langchain_community.utilities import BingSearchAPIWrapper

from lfx.base.langchain_utilities.model import LCToolComponent
from lfx.field_typing import Tool
from lfx.inputs.inputs import IntInput, MessageTextInput, MultilineInput, SecretStrInput
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame
from lfx.template.field.base import Output


class BingSearchAPIComponent(LCToolComponent):
    """`Bing Search API` 工具组件

    契约：
    - 输入：订阅密钥、查询文本、结果数量与可选搜索 URL
    - 输出：`DataFrame` 或 `Tool` 实例
    - 副作用：执行外部搜索请求并更新 `self.status`
    - 失败语义：请求失败时抛出底层异常
    """
    display_name = "Bing Search API"
    description = "Call the Bing Search API."
    name = "BingSearchAPI"
    icon = "Bing"

    inputs = [
        SecretStrInput(name="bing_subscription_key", display_name="Bing Subscription Key"),
        MultilineInput(
            name="input_value",
            display_name="Input",
        ),
        MessageTextInput(name="bing_search_url", display_name="Bing Search URL", advanced=True),
        IntInput(name="k", display_name="Number of results", value=4, required=True),
    ]

    outputs = [
        Output(display_name="DataFrame", name="dataframe", method="fetch_content_dataframe"),
        Output(display_name="Tool", name="tool", method="build_tool"),
    ]

    def run_model(self) -> DataFrame:
        """执行搜索并返回 `DataFrame`

        契约：
        - 输入：无（使用组件字段）
        - 输出：`DataFrame` 实例
        - 副作用：触发外部搜索请求
        - 失败语义：请求失败时抛异常
        """
        return self.fetch_content_dataframe()

    def fetch_content(self) -> list[Data]:
        """调用 `Bing` 搜索并返回 `Data` 列表

        关键路径（三步）：
        1) 根据是否提供 `bing_search_url` 构建 wrapper
        2) 调用 `results` 获取检索结果
        3) 封装为 `Data` 列表并更新状态

        异常流：网络或鉴权失败抛出异常。
        性能瓶颈：外部搜索请求延迟。
        排障入口：`BingSearchAPIWrapper` 异常信息。
        
        契约：
        - 输入：无（使用组件字段）
        - 输出：`Data` 列表
        - 副作用：设置 `self.status`
        - 失败语义：请求失败时抛异常
        """
        if self.bing_search_url:
            wrapper = BingSearchAPIWrapper(
                bing_search_url=self.bing_search_url, bing_subscription_key=self.bing_subscription_key
            )
        else:
            wrapper = BingSearchAPIWrapper(bing_subscription_key=self.bing_subscription_key)
        results = wrapper.results(query=self.input_value, num_results=self.k)
        data = [Data(data=result, text=result["snippet"]) for result in results]
        self.status = data
        return data

    def fetch_content_dataframe(self) -> DataFrame:
        """将搜索结果封装为 `DataFrame`

        契约：
        - 输入：无
        - 输出：`DataFrame` 实例
        - 副作用：触发一次搜索
        - 失败语义：搜索失败时抛异常
        """
        data = self.fetch_content()
        return DataFrame(data)

    def build_tool(self) -> Tool:
        """构建 `LangChain` 工具实例

        契约：
        - 输入：无
        - 输出：`Tool` 实例
        - 副作用：无（仅组装 wrapper）
        - 失败语义：构建失败时抛异常
        """
        if self.bing_search_url:
            wrapper = BingSearchAPIWrapper(
                bing_search_url=self.bing_search_url, bing_subscription_key=self.bing_subscription_key
            )
        else:
            wrapper = BingSearchAPIWrapper(bing_subscription_key=self.bing_subscription_key)
        return cast("Tool", BingSearchResults(api_wrapper=wrapper, num_results=self.k))
