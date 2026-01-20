"""
模块名称：ScrapeGraph Markdownify 组件

本模块提供 ScrapeGraph Markdownify API 的封装，主要用于将网页内容转换为 Markdown。主要功能包括：
- 组装 API Key 与 URL 输入
- 调用 Markdownify 接口获取结构化文本
- 将返回结果封装为 `Data`

关键组件：
- `ScrapeGraphMarkdownifyApi`：组件主体
- `scrape`：调用 Markdownify 接口并返回数据

设计背景：统一 Web 内容抓取与 Markdown 化能力，便于下游处理。
使用场景：给定 URL 获取 Markdown 内容用于检索或摘要。
注意事项：依赖 `scrapegraph-py`；请求失败会抛异常；日志级别会被设置为 INFO。
"""

from lfx.custom.custom_component.component import Component
from lfx.io import (
    MessageTextInput,
    Output,
    SecretStrInput,
)
from lfx.schema.data import Data


class ScrapeGraphMarkdownifyApi(Component):
    """ScrapeGraph Markdownify API 组件封装。

    契约：输入 `api_key` 与 `url`；输出 `list[Data]`，其中 `data` 为 Markdownify 响应。
    副作用：发起网络请求；设置 ScrapeGraph SDK 日志级别。
    失败语义：缺少依赖抛 `ImportError`；调用失败异常原样上抛。
    """
    display_name: str = "ScrapeGraph Markdownify API"
    description: str = "Given a URL, it will return the markdownified content of the website."
    name = "ScrapeGraphMarkdownifyApi"

    output_types: list[str] = ["Document"]
    documentation: str = "https://docs.scrapegraphai.com/services/markdownify"

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
            info="The URL to markdownify.",
        ),
    ]

    outputs = [
        Output(display_name="Data", name="data", method="scrape"),
    ]

    def scrape(self) -> list[Data]:
        """调用 Markdownify 接口并返回结果。

        契约：`url` 必须为有效网页地址；返回的 `Data.data` 为 API 响应内容。
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
            # 实现：发起 Markdownify 请求并在成功后关闭连接。
            response = sgai_client.markdownify(
                website_url=self.url,
            )

            sgai_client.close()

            return Data(data=response)
        except Exception:
            sgai_client.close()
            raise
