"""
模块名称：`RSS` 读取组件

本模块用于抓取并解析 `RSS` 源，输出文章列表的 `DataFrame`。
主要功能包括：
- 拉取 `RSS` 内容并校验 `XML`
- 解析条目并生成结构化数据表

关键组件：
- `RSSReaderComponent`

设计背景：提供简单的 `RSS` 消费入口以支持无鉴权信息源。
注意事项：空响应或无效 `XML` 会返回错误数据表。
"""

import pandas as pd
import requests
from bs4 import BeautifulSoup

from lfx.custom import Component
from lfx.io import IntInput, MessageTextInput, Output
from lfx.log.logger import logger
from lfx.schema import DataFrame


class RSSReaderComponent(Component):
    """`RSS` 读取组件

    契约：
    - 输入：`RSS` 源地址与超时时间
    - 输出：`DataFrame`
    - 副作用：更新 `self.status`
    - 失败语义：请求或解析失败时返回错误 `DataFrame`
    """
    display_name = "RSS Reader"
    description = "Fetches and parses an RSS feed."
    documentation: str = "https://docs.langflow.org/web-search"
    icon = "rss"
    name = "RSSReaderSimple"
    legacy = True
    replacement = "data.WebSearch"

    inputs = [
        MessageTextInput(
            name="rss_url",
            display_name="RSS Feed URL",
            info="URL of the RSS feed to parse.",
            tool_mode=True,
            required=True,
        ),
        IntInput(
            name="timeout",
            display_name="Timeout",
            info="Timeout for the RSS feed request.",
            value=5,
            advanced=True,
        ),
    ]

    outputs = [Output(name="articles", display_name="Articles", method="read_rss")]

    def read_rss(self) -> DataFrame:
        """读取 `RSS` 并返回 `DataFrame`

        关键路径（三步）：
        1) 请求 `RSS` 内容并校验响应
        2) 解析 `XML` 获取条目
        3) 构造并返回 `DataFrame`

        异常流：请求失败或 `XML` 无效时返回错误表。
        性能瓶颈：外部请求延迟。
        排障入口：`self.status` 与异常信息。
        
        契约：
        - 输入：无（使用组件字段）
        - 输出：`DataFrame`
        - 副作用：更新 `self.status`
        - 失败语义：失败时返回错误 `DataFrame`
        """
        try:
            response = requests.get(self.rss_url, timeout=self.timeout)
            response.raise_for_status()
            if not response.content.strip():
                msg = "Empty response received"
                raise ValueError(msg)
            # 注意：校验响应是否为有效 `XML`
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
        logger.info(f"Fetched {len(df_articles)} articles.")
        return DataFrame(df_articles)
