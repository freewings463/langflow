"""
模块名称：firecrawl_map_api

本模块提供 Firecrawl Map API 组件，用于从 URL 生成站点链接映射。
主要功能包括：
- 功能1：解析并校验 URL 列表。
- 功能2：调用 Map API 汇总链接列表。

使用场景：快速获取站点可访问链接列表。
关键组件：
- 类 `FirecrawlMapApi`

设计背景：将 Map API 封装为组件，便于流程内批量扫描。
注意事项：返回结果为合并链接列表，可能包含重复项。
"""

from lfx.custom.custom_component.component import Component
from lfx.io import (
    BoolInput,
    MultilineInput,
    Output,
    SecretStrInput,
)
from lfx.schema.data import Data


class FirecrawlMapApi(Component):
    """Firecrawl Map API 组件。

    契约：输入 `api_key/urls` 等参数；输出 `Data(links=...)`。
    关键路径：
    1) 校验 URL 列表；
    2) 逐个调用 `map_url`；
    3) 汇总链接并返回。
    异常流：URL 为空或无效时抛 `ValueError`。
    排障入口：返回的 `links` 列表可用于检查抓取范围。
    决策：
    问题：多个 URL 需要合并结果便于下游处理。
    方案：逐 URL 扫描并合并链接。
    代价：重复链接可能未去重。
    重评：当需要去重或保留来源 URL 时。
    """
    display_name: str = "Firecrawl Map API"
    description: str = "Maps a URL and returns the results."
    name = "FirecrawlMapApi"

    documentation: str = "https://docs.firecrawl.dev/api-reference/endpoint/map"

    inputs = [
        SecretStrInput(
            name="api_key",
            display_name="Firecrawl API Key",
            required=True,
            password=True,
            info="The API key to use Firecrawl API.",
        ),
        MultilineInput(
            name="urls",
            display_name="URLs",
            required=True,
            info="List of URLs to create maps from (separated by commas or new lines).",
            tool_mode=True,
        ),
        BoolInput(
            name="ignore_sitemap",
            display_name="Ignore Sitemap",
            info="When true, the sitemap.xml file will be ignored during crawling.",
        ),
        BoolInput(
            name="sitemap_only",
            display_name="Sitemap Only",
            info="When true, only links found in the sitemap will be returned.",
        ),
        BoolInput(
            name="include_subdomains",
            display_name="Include Subdomains",
            info="When true, subdomains of the provided URL will also be scanned.",
        ),
    ]

    outputs = [
        Output(display_name="Data", name="data", method="map"),
    ]

    def map(self) -> Data:
        """执行 Map 并返回链接集合。

        契约：返回 `Data(data={"success": True, "links": [...]})`。
        关键路径：校验 URLs -> 构建参数 -> 调用 API -> 汇总链接。
        异常流：依赖缺失抛 `ImportError`；URL 校验失败抛 `ValueError`。
        决策：
        问题：输入可能包含多行或逗号分隔 URL。
        方案：统一拆分并清理空白。
        代价：无。
        重评：当需要保留原始分隔格式时。
        """
        try:
            from firecrawl import FirecrawlApp
        except ImportError as e:
            msg = "Could not import firecrawl integration package. Please install it with `pip install firecrawl-py`."
            raise ImportError(msg) from e

        # 注意：URL 列表为空无法执行 map。
        if not self.urls:
            msg = "URLs are required"
            raise ValueError(msg)

        # 实现：支持逗号与换行分隔的 URL 输入。
        urls = [url.strip() for url in self.urls.replace("\n", ",").split(",") if url.strip()]
        if not urls:
            msg = "No valid URLs provided"
            raise ValueError(msg)

        params = {
            "ignoreSitemap": self.ignore_sitemap,
            "sitemapOnly": self.sitemap_only,
            "includeSubdomains": self.include_subdomains,
        }

        app = FirecrawlApp(api_key=self.api_key)

        # 实现：逐 URL 扫描并合并链接列表。
        combined_links = []
        for url in urls:
            result = app.map_url(url, params=params)
            if isinstance(result, dict) and "links" in result:
                combined_links.extend(result["links"])

        map_result = {"success": True, "links": combined_links}

        return Data(data=map_result)
