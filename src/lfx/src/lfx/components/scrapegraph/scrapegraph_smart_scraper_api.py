"""
模块名称：ScrapeGraph Smart Scraper 组件

本模块提供 ScrapeGraph SmartScraper API 的封装，主要用于根据 URL 与提示词抽取结构化数据。主要功能包括：
- 组装 API Key、URL 与提示词
- 调用 SmartScraper 接口获取结构化结果
- 将返回结果封装为 `Data`

关键组件：
- `ScrapeGraphSmartScraperApi`：组件主体
- `scrape`：调用 SmartScraper 接口并返回数据

设计背景：将网页抽取与结构化输出标准化，便于后续流程处理。
使用场景：需要从网页中按提示词抽取结构化字段。
注意事项：依赖 `scrapegraph-py`；请求失败会抛异常；日志级别会被设置为 INFO。
"""

from lfx.custom.custom_component.component import Component
from lfx.io import (
    MessageTextInput,
    Output,
    SecretStrInput,
)
from lfx.schema.data import Data


class ScrapeGraphSmartScraperApi(Component):
    """ScrapeGraph SmartScraper API 组件封装。

    契约：输入 `api_key`/`url`/`prompt`；输出 `list[Data]`，其中 `data` 为结构化结果。
    副作用：发起网络请求；设置 ScrapeGraph SDK 日志级别。
    失败语义：缺少依赖抛 `ImportError`；调用失败异常原样上抛。
    """
    display_name: str = "ScrapeGraph Smart Scraper API"
    description: str = "Given a URL, it will return the structured data of the website."
    name = "ScrapeGraphSmartScraperApi"

    output_types: list[str] = ["Document"]
    documentation: str = "https://docs.scrapegraphai.com/services/smartscraper"

    inputs = [
        SecretStrInput(
            name="api_key",
            display_name="ScrapeGraph API Key",
            required=True,
            password=True,
            info="The API key to use ScrapeGraph API.",
        ),
        MessageTextInput(
            name="url",
            display_name="URL",
            tool_mode=True,
            info="The URL to scrape.",
        ),
        MessageTextInput(
            name="prompt",
            display_name="Prompt",
            tool_mode=True,
            info="The prompt to use for the smart scraper.",
        ),
    ]

    outputs = [
        Output(display_name="Data", name="data", method="scrape"),
    ]

    def scrape(self) -> list[Data]:
        """调用 SmartScraper 接口并返回结果。

        契约：`url` 与 `prompt` 共同决定抽取策略；返回的 `Data.data` 为 API 响应内容。
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
            # 实现：发起 SmartScraper 请求并在成功后关闭连接。
            response = sgai_client.smartscraper(
                website_url=self.url,
                user_prompt=self.prompt,
            )

            sgai_client.close()

            return Data(data=response)
        except Exception:
            sgai_client.close()
            raise
