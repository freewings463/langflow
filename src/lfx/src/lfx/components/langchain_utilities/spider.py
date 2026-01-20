"""模块名称：Spider 爬取组件

本模块封装 Spider API 的爬取/抓取能力，支持按模式获取网页内容并输出 `Data`。
主要功能包括：组装参数、调用 Spider API、转换结果为结构化数据。

关键组件：
- `SpiderTool`：Spider API 组件入口
- `SpiderToolError`：组件自定义异常类型

设计背景：在 Langflow 中统一接入 Spider 的抓取能力。
注意事项：`params` 一旦提供将覆盖其他输入参数。
"""

from spider.spider import Spider

from lfx.base.langchain_utilities.spider_constants import MODES
from lfx.custom.custom_component.component import Component
from lfx.io import (
    BoolInput,
    DictInput,
    DropdownInput,
    IntInput,
    Output,
    SecretStrInput,
    StrInput,
)
from lfx.schema.data import Data


class SpiderTool(Component):
    """Spider API 组件。

    契约：输入 `spider_api_key/url/mode/params` 等配置；输出 `list[Data]`；
    副作用：调用外部 Spider API；失败语义：非法 `mode` 抛 `ValueError`。
    关键路径：1) 组装参数 2) 调用 `scrape` 或 `crawl` 3) 转换响应为 `Data`。
    决策：`params` 优先级最高
    问题：高级用户需要完整参数控制
    方案：直接透传 `params["data"]`
    代价：其他输入字段将被忽略
    重评：当需要部分覆盖时改为合并策略
    """
    display_name: str = "Spider Web Crawler & Scraper"
    description: str = "Spider API for web crawling and scraping."
    output_types: list[str] = ["Document"]
    documentation: str = "https://spider.cloud/docs/api"

    inputs = [
        SecretStrInput(
            name="spider_api_key",
            display_name="Spider API Key",
            required=True,
            password=True,
            info="The Spider API Key, get it from https://spider.cloud",
        ),
        StrInput(
            name="url",
            display_name="URL",
            required=True,
            info="The URL to scrape or crawl",
        ),
        DropdownInput(
            name="mode",
            display_name="Mode",
            required=True,
            options=MODES,
            value=MODES[0],
            info="The mode of operation: scrape or crawl",
        ),
        IntInput(
            name="limit",
            display_name="Limit",
            info="The maximum amount of pages allowed to crawl per website. Set to 0 to crawl all pages.",
            advanced=True,
        ),
        IntInput(
            name="depth",
            display_name="Depth",
            info="The crawl limit for maximum depth. If 0, no limit will be applied.",
            advanced=True,
        ),
        StrInput(
            name="blacklist",
            display_name="Blacklist",
            info="Blacklist paths that you do not want to crawl. Use Regex patterns.",
            advanced=True,
        ),
        StrInput(
            name="whitelist",
            display_name="Whitelist",
            info="Whitelist paths that you want to crawl, ignoring all other routes. Use Regex patterns.",
            advanced=True,
        ),
        BoolInput(
            name="readability",
            display_name="Use Readability",
            info="Use readability to pre-process the content for reading.",
            advanced=True,
        ),
        IntInput(
            name="request_timeout",
            display_name="Request Timeout",
            info="Timeout for the request in seconds.",
            advanced=True,
        ),
        BoolInput(
            name="metadata",
            display_name="Metadata",
            info="Include metadata in the response.",
            advanced=True,
        ),
        DictInput(
            name="params",
            display_name="Additional Parameters",
            info="Additional parameters to pass to the API. If provided, other inputs will be ignored.",
        ),
    ]

    outputs = [
        Output(display_name="Markdown", name="content", method="crawl"),
    ]

    def crawl(self) -> list[Data]:
        """执行抓取或爬取并返回结果列表。

        关键路径（三步）：
        1) 生成请求参数（含默认值）
        2) 根据 `mode` 调用 Spider API
        3) 解析返回记录并转换为 `Data`

        异常流：`mode` 非法抛 `ValueError`；API 调用异常透传。
        排障入口：Spider API 返回的错误信息。
        决策：`scrape` 强制 `limit=1`
        问题：单页抓取不应多页输出
        方案：在 `scrape` 分支覆盖 `limit`
        代价：无法在 scrape 模式抓多页
        重评：当 Spider 支持分页抓取时开放配置
        """
        if self.params:
            parameters = self.params["data"]
        else:
            parameters = {
                "limit": self.limit or None,
                "depth": self.depth or None,
                "blacklist": self.blacklist or None,
                "whitelist": self.whitelist or None,
                "readability": self.readability,
                "request_timeout": self.request_timeout or None,
                "metadata": self.metadata,
                "return_format": "markdown",
            }

        app = Spider(api_key=self.spider_api_key)
        if self.mode == "scrape":
            parameters["limit"] = 1
            result = app.scrape_url(self.url, parameters)
        elif self.mode == "crawl":
            result = app.crawl_url(self.url, parameters)
        else:
            msg = f"Invalid mode: {self.mode}. Must be 'scrape' or 'crawl'."
            raise ValueError(msg)

        records = []

        for record in result:
            if self.metadata:
                records.append(
                    Data(
                        data={
                            "content": record["content"],
                            "url": record["url"],
                            "metadata": record["metadata"],
                        }
                    )
                )
            else:
                records.append(Data(data={"content": record["content"], "url": record["url"]}))
        return records


class SpiderToolError(Exception):
    """SpiderTool 专用异常类型。

    契约：用于标识 Spider 组件相关错误；副作用无。
    决策：保留独立异常类型以便上游区分
    问题：需要与其他组件错误区分
    方案：单独定义异常类
    代价：未在当前流程中使用
    重评：当引入统一错误体系时合并
    """
