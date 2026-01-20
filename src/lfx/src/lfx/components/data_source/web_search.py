"""
模块名称：统一搜索组件

本模块将网页搜索、新闻搜索与 `RSS` 读取合并为一个组件，通过模式切换实现不同检索能力。
主要功能包括：
- `Web` 模式：`DuckDuckGo` 网页搜索
- `News` 模式：`Google News` `RSS` 检索
- `RSS` 模式：直接解析订阅源

关键组件：
- `WebSearchComponent`

设计背景：降低组件数量，统一入口并提升配置一致性。
注意事项：不同模式下输入字段含义不同，需关注 `search_mode`。
"""

import re
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup

from lfx.custom import Component
from lfx.io import IntInput, MessageTextInput, Output, TabInput
from lfx.schema import DataFrame
from lfx.utils.request_utils import get_user_agent


class WebSearchComponent(Component):
    """统一搜索组件

    契约：
    - 输入：搜索模式与对应参数
    - 输出：`DataFrame`
    - 副作用：发起外部请求并记录日志
    - 失败语义：请求失败时返回错误 `DataFrame`
    """
    display_name = "Web Search"
    description = "Search the web, news, or RSS feeds."
    documentation: str = "https://docs.langflow.org/web-search"
    icon = "search"
    name = "UnifiedWebSearch"

    inputs = [
        TabInput(
            name="search_mode",
            display_name="Search Mode",
            options=["Web", "News", "RSS"],
            info="Choose search mode: Web (DuckDuckGo), News (Google News), or RSS (Feed Reader)",
            value="Web",
            real_time_refresh=True,
            tool_mode=True,
        ),
        MessageTextInput(
            name="query",
            display_name="Search Query",
            info="Search keywords for news articles.",
            tool_mode=True,
            required=True,
        ),
        MessageTextInput(
            name="hl",
            display_name="Language (hl)",
            info="Language code, e.g. en-US, fr, de. Default: en-US.",
            tool_mode=False,
            input_types=[],
            required=False,
            advanced=True,
        ),
        MessageTextInput(
            name="gl",
            display_name="Country (gl)",
            info="Country code, e.g. US, FR, DE. Default: US.",
            tool_mode=False,
            input_types=[],
            required=False,
            advanced=True,
        ),
        MessageTextInput(
            name="ceid",
            display_name="Country:Language (ceid)",
            info="e.g. US:en, FR:fr. Default: US:en.",
            tool_mode=False,
            value="US:en",
            input_types=[],
            required=False,
            advanced=True,
        ),
        MessageTextInput(
            name="topic",
            display_name="Topic",
            info="One of: WORLD, NATION, BUSINESS, TECHNOLOGY, ENTERTAINMENT, SCIENCE, SPORTS, HEALTH.",
            tool_mode=False,
            input_types=[],
            required=False,
            advanced=True,
        ),
        MessageTextInput(
            name="location",
            display_name="Location (Geo)",
            info="City, state, or country for location-based news. Leave blank for keyword search.",
            tool_mode=False,
            input_types=[],
            required=False,
            advanced=True,
        ),
        IntInput(
            name="timeout",
            display_name="Timeout",
            info="Timeout for the request in seconds.",
            value=5,
            required=False,
            advanced=True,
        ),
    ]

    outputs = [Output(name="results", display_name="Results", method="perform_search")]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def update_build_config(self, build_config: dict, field_value: Any, field_name: str | None = None) -> dict:
        """根据搜索模式更新输入字段

        契约：
        - 输入：构建配置、字段值与字段名
        - 输出：更新后的构建配置
        - 副作用：更新字段描述与显示名
        - 失败语义：无
        """
        if field_name == "search_mode":
            # 注意：根据模式控制字段信息
            is_news = field_value == "News"
            is_rss = field_value == "RSS"

            # 注意：按模式更新查询字段说明
            if is_rss:
                build_config["query"]["info"] = "RSS feed URL to parse"
                build_config["query"]["display_name"] = "RSS Feed URL"
            elif is_news:
                build_config["query"]["info"] = "Search keywords for news articles."
                build_config["query"]["display_name"] = "Search Query"
            else:  # `Web` 模式
                build_config["query"]["info"] = "Keywords to search for"
                build_config["query"]["display_name"] = "Search Query"

            # 注意：新闻相关字段保持 `advanced=True`，与原组件一致

        return build_config

    def validate_url(self, string: str) -> bool:
        """校验 `URL` 格式

        契约：
        - 输入：字符串
        - 输出：`bool`
        - 副作用：无
        - 失败语义：无
        """
        url_regex = re.compile(
            r"^(https?:\/\/)?" r"(www\.)?" r"([a-zA-Z0-9.-]+)" r"(\.[a-zA-Z]{2,})?" r"(:\d+)?" r"(\/[^\s]*)?$",
            re.IGNORECASE,
        )
        return bool(url_regex.match(string))

    def ensure_url(self, url: str) -> str:
        """确保 `URL` 具有协议前缀

        契约：
        - 输入：`URL` 字符串
        - 输出：规范化 `URL`
        - 副作用：无
        - 失败语义：无效 `URL` 时抛 `ValueError`
        """
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        if not self.validate_url(url):
            msg = f"Invalid URL: {url}"
            raise ValueError(msg)
        return url

    def _sanitize_query(self, query: str) -> str:
        """清理搜索词中的危险字符

        契约：
        - 输入：搜索词
        - 输出：清理后的搜索词
        - 副作用：无
        - 失败语义：无
        """
        return re.sub(r'[<>"\']', "", query.strip())

    def clean_html(self, html_string: str) -> str:
        """移除 `HTML` 标签并返回纯文本

        契约：
        - 输入：`HTML` 字符串
        - 输出：纯文本
        - 副作用：无
        - 失败语义：无
        """
        return BeautifulSoup(html_string, "html.parser").get_text(separator=" ", strip=True)

    def perform_web_search(self) -> DataFrame:
        """执行 `DuckDuckGo` 网页搜索

        关键路径（三步）：
        1) 清理查询词并构建请求
        2) 解析搜索结果页
        3) 拉取目标页面并返回内容

        异常流：请求失败时返回错误 `DataFrame`。
        性能瓶颈：多次网络请求。
        排障入口：`self.status` 与异常信息。
        
        契约：
        - 输入：无（使用组件字段）
        - 输出：`DataFrame`
        - 副作用：发起外部请求
        - 失败语义：请求失败时返回错误 `DataFrame`
        """
        query = self._sanitize_query(self.query)
        if not query:
            msg = "Empty search query"
            raise ValueError(msg)

        headers = {"User-Agent": get_user_agent()}
        params = {"q": query, "kl": "us-en"}
        url = "https://html.duckduckgo.com/html/"

        try:
            response = requests.get(url, params=params, headers=headers, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as e:
            self.status = f"Failed request: {e!s}"
            return DataFrame(pd.DataFrame([{"title": "Error", "link": "", "snippet": str(e), "content": ""}]))

        if not response.text or "text/html" not in response.headers.get("content-type", "").lower():
            self.status = "No results found"
            return DataFrame(
                pd.DataFrame([{"title": "Error", "link": "", "snippet": "No results found", "content": ""}])
            )

        soup = BeautifulSoup(response.text, "html.parser")
        results = []

        for result in soup.select("div.result"):
            title_tag = result.select_one("a.result__a")
            snippet_tag = result.select_one("a.result__snippet")
            if title_tag:
                raw_link = title_tag.get("href", "")
                parsed = urlparse(raw_link)
                uddg = parse_qs(parsed.query).get("uddg", [""])[0]
                decoded_link = unquote(uddg) if uddg else raw_link

                try:
                    final_url = self.ensure_url(decoded_link)
                    page = requests.get(final_url, headers=headers, timeout=self.timeout)
                    page.raise_for_status()
                    content = BeautifulSoup(page.text, "lxml").get_text(separator=" ", strip=True)
                except requests.RequestException as e:
                    final_url = decoded_link
                    content = f"(Failed to fetch: {e!s}"

                results.append(
                    {
                        "title": title_tag.get_text(strip=True),
                        "link": final_url,
                        "snippet": snippet_tag.get_text(strip=True) if snippet_tag else "",
                        "content": content,
                    }
                )

        return DataFrame(pd.DataFrame(results))

    def perform_news_search(self) -> DataFrame:
        """执行 `Google News` 搜索

        契约：
        - 输入：无（使用组件字段）
        - 输出：`DataFrame`
        - 副作用：发起外部请求
        - 失败语义：请求失败时返回错误 `DataFrame`
        """
        query = getattr(self, "query", "")
        hl = getattr(self, "hl", "en-US") or "en-US"
        gl = getattr(self, "gl", "US") or "US"
        topic = getattr(self, "topic", None)
        location = getattr(self, "location", None)

        ceid = f"{gl}:{hl.split('-')[0]}"

        # 根据参数构建 `RSS` `URL`
        if topic:
            # 主题订阅
            base_url = f"https://news.google.com/rss/headlines/section/topic/{quote_plus(topic.upper())}"
            params = f"?hl={hl}&gl={gl}&ceid={ceid}"
            rss_url = base_url + params
        elif location:
            # 地理位置订阅
            base_url = f"https://news.google.com/rss/headlines/section/geo/{quote_plus(location)}"
            params = f"?hl={hl}&gl={gl}&ceid={ceid}"
            rss_url = base_url + params
        elif query:
            # 关键词搜索订阅
            base_url = "https://news.google.com/rss/search?q="
            query_encoded = quote_plus(query)
            params = f"&hl={hl}&gl={gl}&ceid={ceid}"
            rss_url = f"{base_url}{query_encoded}{params}"
        else:
            self.status = "No search query, topic, or location provided."
            return DataFrame(
                pd.DataFrame(
                    [{"title": "Error", "link": "", "published": "", "summary": "No search parameters provided"}]
                )
            )

        try:
            response = requests.get(rss_url, timeout=self.timeout)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, "xml")
            items = soup.find_all("item")
        except requests.RequestException as e:
            self.status = f"Failed to fetch news: {e}"
            return DataFrame(pd.DataFrame([{"title": "Error", "link": "", "published": "", "summary": str(e)}]))

        if not items:
            self.status = "No news articles found."
            return DataFrame(pd.DataFrame([{"title": "No articles found", "link": "", "published": "", "summary": ""}]))

        articles = []
        for item in items:
            try:
                title = self.clean_html(item.title.text if item.title else "")
                link = item.link.text if item.link else ""
                published = item.pubDate.text if item.pubDate else ""
                summary = self.clean_html(item.description.text if item.description else "")
                articles.append({"title": title, "link": link, "published": published, "summary": summary})
            except (AttributeError, ValueError, TypeError) as e:
                self.log(f"Error parsing article: {e!s}")
                continue

        return DataFrame(pd.DataFrame(articles))

    def perform_rss_read(self) -> DataFrame:
        """读取 `RSS` 订阅

        契约：
        - 输入：无（使用组件字段）
        - 输出：`DataFrame`
        - 副作用：发起外部请求
        - 失败语义：请求失败时返回错误 `DataFrame`
        """
        rss_url = getattr(self, "query", "")
        if not rss_url:
            return DataFrame(
                pd.DataFrame([{"title": "Error", "link": "", "published": "", "summary": "No RSS URL provided"}])
            )

        try:
            response = requests.get(rss_url, timeout=self.timeout)
            response.raise_for_status()
            if not response.content.strip():
                msg = "Empty response received"
                raise ValueError(msg)

            # 校验 `XML` 有效性
            try:
                BeautifulSoup(response.content, "xml")
            except Exception as e:
                msg = f"Invalid XML response: {e}"
                raise ValueError(msg) from e

            soup = BeautifulSoup(response.content, "xml")
            items = soup.find_all("item")
        except (requests.RequestException, ValueError) as e:
            self.status = f"Failed to fetch RSS: {e}"
            return DataFrame(pd.DataFrame([{"title": "Error", "link": "", "published": "", "summary": str(e)}]))

        articles = [
            {
                "title": item.title.text if item.title else "",
                "link": item.link.text if item.link else "",
                "published": item.pubDate.text if item.pubDate else "",
                "summary": item.description.text if item.description else "",
            }
            for item in items
        ]

        # 注意：即使为空也保持固定列结构
        df_articles = pd.DataFrame(articles, columns=["title", "link", "published", "summary"])
        self.log(f"Fetched {len(df_articles)} articles.")
        return DataFrame(df_articles)

    def perform_search(self) -> DataFrame:
        """根据模式路由到对应搜索实现

        契约：
        - 输入：无
        - 输出：`DataFrame`
        - 副作用：发起外部请求
        - 失败语义：未知模式回退到网页搜索
        """
        search_mode = getattr(self, "search_mode", "Web")

        if search_mode == "Web":
            return self.perform_web_search()
        if search_mode == "News":
            return self.perform_news_search()
        if search_mode == "RSS":
            return self.perform_rss_read()
        # 兜底回退到网页搜索
        return self.perform_web_search()
