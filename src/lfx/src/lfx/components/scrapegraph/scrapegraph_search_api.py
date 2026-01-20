"""
模块名称：ScrapeGraph Search 组件

本模块提供 ScrapeGraph SearchScraper API 的封装，主要用于根据搜索提示词返回检索结果。主要功能包括：
- 组装 API Key 与搜索提示词
- 调用 SearchScraper 接口获取搜索结果
- 将返回结果封装为 `Data`

关键组件：
- `ScrapeGraphSearchApi`：组件主体
- `search`：调用 SearchScraper 接口并返回数据

设计背景：统一检索入口，避免在流程中手写外部搜索调用。
使用场景：给定搜索提示词获取结构化搜索结果。
注意事项：依赖 `scrapegraph-py`；请求失败会抛异常；日志级别会被设置为 INFO。
"""

from lfx.custom.custom_component.component import Component
from lfx.io import (
    MessageTextInput,
    Output,
    SecretStrInput,
)
from lfx.schema.data import Data


class ScrapeGraphSearchApi(Component):
    """ScrapeGraph SearchScraper API 组件封装。

    契约：输入 `api_key` 与 `user_prompt`；输出 `list[Data]`，其中 `data` 为搜索结果。
    副作用：发起网络请求；设置 ScrapeGraph SDK 日志级别。
    失败语义：缺少依赖抛 `ImportError`；调用失败异常原样上抛。
    """
    display_name: str = "ScrapeGraph Search API"
    description: str = "Given a search prompt, it will return search results using ScrapeGraph's search functionality."
    name = "ScrapeGraphSearchApi"

    documentation: str = "https://docs.scrapegraphai.com/services/searchscraper"
    icon = "ScrapeGraph"

    inputs = [
        SecretStrInput(
            name="api_key",
            display_name="ScrapeGraph API Key",
            required=True,
            password=True,
            info="The API key to use ScrapeGraph API.",
        ),
        MessageTextInput(
            name="user_prompt",
            display_name="Search Prompt",
            tool_mode=True,
            info="The search prompt to use.",
        ),
    ]

    outputs = [
        Output(display_name="Data", name="data", method="search"),
    ]

    def search(self) -> list[Data]:
        """调用 SearchScraper 接口并返回结果。

        契约：`user_prompt` 作为搜索输入；返回的 `Data.data` 为 API 响应内容。
        副作用：创建并关闭 ScrapeGraph 客户端连接。
        失败语义：SDK 导入失败抛 `ImportError`；API 异常原样上抛并确保关闭连接。
        """
        try:
            from scrapegraph_py import Client
            from scrapegraph_py.logger import sgai_logger
        except ImportError as e:
            msg = "Could not import scrapegraph-py package. Please install it with `pip install scrapegraph-py`."
            raise ImportError(msg) from e

        # 注意：ScrapeGraph SDK 日志级别为全局设置。
        sgai_logger.set_logging(level="INFO")

        # 实现：使用 API Key 初始化客户端。
        sgai_client = Client(api_key=self.api_key)

        try:
            # 实现：发起 SearchScraper 请求并在成功后关闭连接。
            response = sgai_client.searchscraper(
                user_prompt=self.user_prompt,
            )

            sgai_client.close()

            return Data(data=response)
        except Exception:
            sgai_client.close()
            raise
