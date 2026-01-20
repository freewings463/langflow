"""
模块名称：firecrawl_crawl_api

本模块提供 Firecrawl Crawl API 组件，用于站点级爬取并返回结果集合。
主要功能包括：
- 功能1：构建 crawl 请求参数并设置默认值。
- 功能2：调用 Firecrawl Crawl API 并返回结果。

使用场景：需要对站点进行广度爬取并获取批量内容时。
关键组件：
- 类 `FirecrawlCrawlApi`

设计背景：将 Firecrawl Crawl 调用封装为组件，便于流程化使用。
注意事项：未提供 idempotency key 时会自动生成；默认仅抓取主体内容。
"""

import uuid

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, IntInput, MultilineInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data


class FirecrawlCrawlApi(Component):
    """Firecrawl Crawl API 组件。

    契约：输入 `api_key/url` 等参数；输出 `Data(results=...)`。
    关键路径：
    1) 解析 crawler/scrape 选项并补齐默认值；
    2) 构建 `FirecrawlApp` 并调用 `crawl_url`；
    3) 返回封装后的 `Data`。
    异常流：依赖缺失时抛 `ImportError`。
    排障入口：调用方可通过返回的 `results` 结构排查失败原因。
    决策：
    问题：Crawl 参数繁多且需要与 v1 默认保持一致。
    方案：在组件内统一设置默认值与 `onlyMainContent`。
    代价：默认限制可能导致部分内容未抓取。
    重评：当 Firecrawl 默认值或 API 行为变更时。
    """
    display_name: str = "Firecrawl Crawl API"
    description: str = "Crawls a URL and returns the results."
    name = "FirecrawlCrawlApi"

    documentation: str = "https://docs.firecrawl.dev/v1/api-reference/endpoint/crawl-post"

    inputs = [
        SecretStrInput(
            name="api_key",
            display_name="Firecrawl API Key",
            required=True,
            password=True,
            info="The API key to use Firecrawl API.",
        ),
        MultilineInput(
            name="url",
            display_name="URL",
            required=True,
            info="The URL to scrape.",
            tool_mode=True,
        ),
        IntInput(
            name="timeout",
            display_name="Timeout",
            info="Timeout in milliseconds for the request.",
        ),
        StrInput(
            name="idempotency_key",
            display_name="Idempotency Key",
            info="Optional idempotency key to ensure unique requests.",
        ),
        DataInput(
            name="crawlerOptions",
            display_name="Crawler Options",
            info="The crawler options to send with the request.",
        ),
        DataInput(
            name="scrapeOptions",
            display_name="Scrape Options",
            info="The page options to send with the request.",
        ),
    ]

    outputs = [
        Output(display_name="Data", name="data", method="crawl"),
    ]
    idempotency_key: str | None = None

    def crawl(self) -> Data:
        """执行 Crawl 并返回结果数据。

        契约：返回 `Data`，包含 `results` 字段。
        关键路径：准备参数 -> 设置幂等键 -> 调用 API。
        异常流：依赖缺失抛 `ImportError`；API 错误由 SDK 抛出。
        决策：
        问题：重复请求需要幂等控制避免重复计费。
        方案：未提供时自动生成 `idempotency_key`。
        代价：自动生成无法跨调用复用。
        重评：当上游提供业务级幂等 ID 时。
        """
        try:
            from firecrawl import FirecrawlApp
        except ImportError as e:
            msg = "Could not import firecrawl integration package. Please install it with `pip install firecrawl-py`."
            raise ImportError(msg) from e

        params = self.crawlerOptions.__dict__["data"] if self.crawlerOptions else {}
        scrape_options_dict = self.scrapeOptions.__dict__["data"] if self.scrapeOptions else {}
        if scrape_options_dict:
            params["scrapeOptions"] = scrape_options_dict

        # 注意：v1 新参数默认值，避免调用方未显式设置导致行为变化。
        params.setdefault("maxDepth", 2)
        params.setdefault("limit", 10000)
        params.setdefault("allowExternalLinks", False)
        params.setdefault("allowBackwardLinks", False)
        params.setdefault("ignoreSitemap", False)
        params.setdefault("ignoreQueryParameters", False)

        # 注意：默认仅抓取主要内容，避免噪声过大。
        if "scrapeOptions" in params:
            params["scrapeOptions"].setdefault("onlyMainContent", True)
        else:
            params["scrapeOptions"] = {"onlyMainContent": True}

        if not self.idempotency_key:
            self.idempotency_key = str(uuid.uuid4())

        app = FirecrawlApp(api_key=self.api_key)
        crawl_result = app.crawl_url(self.url, params=params, idempotency_key=self.idempotency_key)
        return Data(data={"results": crawl_result})
