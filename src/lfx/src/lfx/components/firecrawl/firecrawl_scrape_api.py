"""
模块名称：firecrawl_scrape_api

本模块提供 Firecrawl Scrape API 组件，用于单页抓取并返回解析结果。
主要功能包括：
- 功能1：构建抓取与提取参数。
- 功能2：调用 Scrape API 并返回结果。

使用场景：对单个 URL 进行内容抓取与格式化输出。
关键组件：
- 类 `FirecrawlScrapeApi`

设计背景：将 Scrape API 封装为组件，便于流程化调用。
注意事项：默认仅返回主内容且格式为 markdown，可通过参数覆盖。
"""

from lfx.custom.custom_component.component import Component
from lfx.io import (
    DataInput,
    IntInput,
    MultilineInput,
    Output,
    SecretStrInput,
)
from lfx.schema.data import Data


class FirecrawlScrapeApi(Component):
    """Firecrawl Scrape API 组件。

    契约：输入 `api_key/url` 与可选参数；输出 `Data(data=results)`。
    关键路径：
    1) 构建 scrape/extractor 参数；
    2) 设置默认格式与主内容开关；
    3) 调用 `scrape_url` 返回结果。
    异常流：依赖缺失抛 `ImportError`。
    排障入口：返回的 `results` 结构用于定位解析问题。
    决策：
    问题：调用方可能未提供格式与内容范围参数。
    方案：默认使用 `markdown` 且仅主内容。
    代价：可能遗漏页面的非主体内容。
    重评：当业务需要完整页面或不同格式时。
    """
    display_name: str = "Firecrawl Scrape API"
    description: str = "Scrapes a URL and returns the results."
    name = "FirecrawlScrapeApi"

    documentation: str = "https://docs.firecrawl.dev/api-reference/endpoint/scrape"

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
        DataInput(
            name="scrapeOptions",
            display_name="Scrape Options",
            info="The page options to send with the request.",
        ),
        DataInput(
            name="extractorOptions",
            display_name="Extractor Options",
            info="The extractor options to send with the request.",
        ),
    ]

    outputs = [
        Output(display_name="Data", name="data", method="scrape"),
    ]

    def scrape(self) -> Data:
        """执行 Scrape 并返回结果数据。

        契约：返回 `Data(data=results)`。
        关键路径：解析参数 -> 设置默认值 -> 调用 API。
        异常流：依赖缺失抛 `ImportError`；API 错误由 SDK 抛出。
        决策：
        问题：提取器参数需要与 scrape 参数合并。
        方案：将 `extractorOptions` 映射到 `extract` 字段。
        代价：参数结构依赖 Firecrawl API 约定。
        重评：当 API 参数结构调整时。
        """
        try:
            from firecrawl import FirecrawlApp
        except ImportError as e:
            msg = "Could not import firecrawl integration package. Please install it with `pip install firecrawl-py`."
            raise ImportError(msg) from e

        params = self.scrapeOptions.__dict__.get("data", {}) if self.scrapeOptions else {}
        extractor_options_dict = self.extractorOptions.__dict__.get("data", {}) if self.extractorOptions else {}
        if extractor_options_dict:
            params["extract"] = extractor_options_dict

        # 注意：设置默认参数，避免返回格式不确定。
        params.setdefault("formats", ["markdown"])  # 注意：默认输出格式为 markdown。
        params.setdefault("onlyMainContent", True)  # 注意：默认仅抓取主内容。

        app = FirecrawlApp(api_key=self.api_key)
        results = app.scrape_url(self.url, params=params)
        return Data(data=results)
