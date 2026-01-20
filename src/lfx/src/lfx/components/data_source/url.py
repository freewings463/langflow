"""
模块名称：`URL` 内容抓取组件

本模块提供基于 `RecursiveUrlLoader` 的网页抓取与解析能力，支持递归抓取与多种输出格式。
主要功能包括：
- 递归抓取指定 `URL` 列表
- 支持输出 `Text`/`HTML`/`Markdown`
- 将抓取结果封装为 `DataFrame` 或 `Message`

关键组件：
- `URLComponent`

设计背景：提供可配置的网页抓取入口以支撑数据采集场景。
注意事项：抓取受网络环境影响，需合理设置深度与超时。
"""

import importlib
import io
import re

import requests
from bs4 import BeautifulSoup
from langchain_community.document_loaders import RecursiveUrlLoader
from markitdown import MarkItDown

from lfx.custom.custom_component.component import Component
from lfx.field_typing.range_spec import RangeSpec
from lfx.helpers.data import safe_convert
from lfx.io import BoolInput, DropdownInput, IntInput, MessageTextInput, Output, SliderInput, TableInput
from lfx.log.logger import logger
from lfx.schema.dataframe import DataFrame
from lfx.schema.message import Message
from lfx.utils.request_utils import get_user_agent

# 常量配置
DEFAULT_TIMEOUT = 30
DEFAULT_MAX_DEPTH = 1
DEFAULT_FORMAT = "Text"


URL_REGEX = re.compile(
    r"^(https?:\/\/)?" r"(www\.)?" r"([a-zA-Z0-9.-]+)" r"(\.[a-zA-Z]{2,})?" r"(:\d+)?" r"(\/[^\s]*)?$",
    re.IGNORECASE,
)

USER_AGENT = None
# 注意：通过 `importlib.util.find_spec` 判断 `langflow` 是否安装
if importlib.util.find_spec("langflow"):
    langflow_installed = True
    USER_AGENT = get_user_agent()
else:
    langflow_installed = False
    USER_AGENT = "lfx"


class URLComponent(Component):
    """网页抓取与解析组件

    契约：
    - 输入：`URL` 列表与抓取参数
    - 输出：`DataFrame` 或 `Message`
    - 副作用：发起网络请求并记录日志
    - 失败语义：无有效 `URL` 或抓取失败时抛 `ValueError`
    """

    display_name = "URL"
    description = "Fetch content from one or more web pages, following links recursively."
    documentation: str = "https://docs.langflow.org/url"
    icon = "layout-template"
    name = "URLComponent"

    inputs = [
        MessageTextInput(
            name="urls",
            display_name="URLs",
            info="Enter one or more URLs to crawl recursively, by clicking the '+' button.",
            is_list=True,
            tool_mode=True,
            placeholder="Enter a URL...",
            list_add_label="Add URL",
            input_types=[],
        ),
        SliderInput(
            name="max_depth",
            display_name="Depth",
            info=(
                "Controls how many 'clicks' away from the initial page the crawler will go:\n"
                "- depth 1: only the initial page\n"
                "- depth 2: initial page + all pages linked directly from it\n"
                "- depth 3: initial page + direct links + links found on those direct link pages\n"
                "Note: This is about link traversal, not URL path depth."
            ),
            value=DEFAULT_MAX_DEPTH,
            range_spec=RangeSpec(min=1, max=5, step=1),
            required=False,
            min_label=" ",
            max_label=" ",
            min_label_icon="None",
            max_label_icon="None",
            # 注意：可选 `slider_input=True`
        ),
        BoolInput(
            name="prevent_outside",
            display_name="Prevent Outside",
            info=(
                "If enabled, only crawls URLs within the same domain as the root URL. "
                "This helps prevent the crawler from going to external websites."
            ),
            value=True,
            required=False,
            advanced=True,
        ),
        BoolInput(
            name="use_async",
            display_name="Use Async",
            info=(
                "If enabled, uses asynchronous loading which can be significantly faster "
                "but might use more system resources."
            ),
            value=True,
            required=False,
            advanced=True,
        ),
        DropdownInput(
            name="format",
            display_name="Output Format",
            info=(
                "Output Format. Use 'Text' to extract the text from the HTML, "
                "'Markdown' to parse the HTML into Markdown format, or 'HTML' "
                "for the raw HTML content."
            ),
            options=["Text", "HTML", "Markdown"],
            value=DEFAULT_FORMAT,
            advanced=True,
        ),
        IntInput(
            name="timeout",
            display_name="Timeout",
            info="Timeout for the request in seconds.",
            value=DEFAULT_TIMEOUT,
            required=False,
            advanced=True,
        ),
        TableInput(
            name="headers",
            display_name="Headers",
            info="The headers to send with the request",
            table_schema=[
                {
                    "name": "key",
                    "display_name": "Header",
                    "type": "str",
                    "description": "Header name",
                },
                {
                    "name": "value",
                    "display_name": "Value",
                    "type": "str",
                    "description": "Header value",
                },
            ],
            value=[{"key": "User-Agent", "value": USER_AGENT}],
            advanced=True,
            input_types=["DataFrame"],
        ),
        BoolInput(
            name="filter_text_html",
            display_name="Filter Text/HTML",
            info="If enabled, filters out text/css content type from the results.",
            value=True,
            required=False,
            advanced=True,
        ),
        BoolInput(
            name="continue_on_failure",
            display_name="Continue on Failure",
            info="If enabled, continues crawling even if some requests fail.",
            value=True,
            required=False,
            advanced=True,
        ),
        BoolInput(
            name="check_response_status",
            display_name="Check Response Status",
            info="If enabled, checks the response status of the request.",
            value=False,
            required=False,
            advanced=True,
        ),
        BoolInput(
            name="autoset_encoding",
            display_name="Autoset Encoding",
            info="If enabled, automatically sets the encoding of the request.",
            value=True,
            required=False,
            advanced=True,
        ),
    ]

    outputs = [
        Output(display_name="Extracted Pages", name="page_results", method="fetch_content"),
        Output(display_name="Raw Content", name="raw_results", method="fetch_content_as_message", tool_mode=False),
    ]

    @staticmethod
    def _html_extractor(x: str) -> str:
        """提取原始 `HTML` 内容

        契约：
        - 输入：`HTML` 字符串
        - 输出：原始字符串
        - 副作用：无
        - 失败语义：无
        """
        return x

    @staticmethod
    def _text_extractor(x: str) -> str:
        """从 `HTML` 提取纯文本

        契约：
        - 输入：`HTML` 字符串
        - 输出：纯文本字符串
        - 副作用：无
        - 失败语义：无
        """
        return BeautifulSoup(x, "lxml").get_text()

    @staticmethod
    def _markdown_extractor(x: str) -> str:
        """将 `HTML` 转换为 `Markdown`

        契约：
        - 输入：`HTML` 字符串
        - 输出：`Markdown` 字符串
        - 副作用：无
        - 失败语义：无
        """
        stream = io.BytesIO(x.encode("utf-8"))
        result = MarkItDown(enable_plugins=False).convert_stream(stream)
        return result.markdown

    @staticmethod
    def validate_url(url: str) -> bool:
        """校验字符串是否符合 `URL` 规则

        契约：
        - 输入：`URL` 字符串
        - 输出：`bool`
        - 副作用：无
        - 失败语义：无
        """
        return bool(URL_REGEX.match(url))

    def ensure_url(self, url: str) -> str:
        """规范化并校验 `URL`

        契约：
        - 输入：`URL` 字符串
        - 输出：规范化后的 `URL`
        - 副作用：无
        - 失败语义：无效 `URL` 时抛 `ValueError`
        """
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        if not self.validate_url(url):
            msg = f"Invalid URL: {url}"
            raise ValueError(msg)

        return url

    def _create_loader(self, url: str) -> RecursiveUrlLoader:
        """创建 `RecursiveUrlLoader` 实例

        契约：
        - 输入：`URL`
        - 输出：`RecursiveUrlLoader` 实例
        - 副作用：无
        - 失败语义：无
        """
        headers_dict = {header["key"]: header["value"] for header in self.headers if header["value"] is not None}
        extractors = {
            "HTML": self._html_extractor,
            "Markdown": self._markdown_extractor,
            "Text": self._text_extractor,
        }
        extractor = extractors.get(self.format, self._text_extractor)

        return RecursiveUrlLoader(
            url=url,
            max_depth=self.max_depth,
            prevent_outside=self.prevent_outside,
            use_async=self.use_async,
            extractor=extractor,
            timeout=self.timeout,
            headers=headers_dict,
            check_response_status=self.check_response_status,
            continue_on_failure=self.continue_on_failure,
            base_url=url,  # 注意：设置 `base_url` 确保同域抓取
            autoset_encoding=self.autoset_encoding,  # 注意：启用自动编码探测
            exclude_dirs=[],  # 注意：可扩展的排除目录
            link_regex=None,  # 注意：可扩展的链接过滤
        )

    def fetch_url_contents(self) -> list[dict]:
        """抓取并解析 `URL` 内容

        关键路径（三步）：
        1) 规范化并去重 `URL` 列表
        2) 逐个加载并收集文档
        3) 转换为结构化字典列表

        异常流：无有效 `URL` 或全部失败时抛 `ValueError`。
        性能瓶颈：网络请求与解析。
        排障入口：日志与异常信息。
        
        契约：
        - 输入：无（使用组件字段）
        - 输出：字典列表
        - 副作用：记录日志
        - 失败语义：抓取失败时抛 `ValueError`
        """
        try:
            urls = list({self.ensure_url(url) for url in self.urls if url.strip()})
            logger.debug(f"URLs: {urls}")
            if not urls:
                msg = "No valid URLs provided."
                raise ValueError(msg)

            all_docs = []
            for url in urls:
                logger.debug(f"Loading documents from {url}")

                try:
                    loader = self._create_loader(url)
                    docs = loader.load()

                    if not docs:
                        logger.warning(f"No documents found for {url}")
                        continue

                    logger.debug(f"Found {len(docs)} documents from {url}")
                    all_docs.extend(docs)

                except requests.exceptions.RequestException as e:
                    logger.exception(f"Error loading documents from {url}: {e}")
                    continue

            if not all_docs:
                msg = "No documents were successfully loaded from any URL"
                raise ValueError(msg)

            # 注意：将文档转换为结构化数据
            data = [
                {
                    "text": safe_convert(doc.page_content, clean_data=True),
                    "url": doc.metadata.get("source", ""),
                    "title": doc.metadata.get("title", ""),
                    "description": doc.metadata.get("description", ""),
                    "content_type": doc.metadata.get("content_type", ""),
                    "language": doc.metadata.get("language", ""),
                }
                for doc in all_docs
            ]
        except Exception as e:
            error_msg = e.message if hasattr(e, "message") else e
            msg = f"Error loading documents: {error_msg!s}"
            logger.exception(msg)
            raise ValueError(msg) from e
        return data

    def fetch_content(self) -> DataFrame:
        """将抓取结果转换为 `DataFrame`

        契约：
        - 输入：无
        - 输出：`DataFrame`
        - 副作用：触发抓取
        - 失败语义：抓取失败时抛 `ValueError`
        """
        return DataFrame(data=self.fetch_url_contents())

    def fetch_content_as_message(self) -> Message:
        """将抓取结果转换为 `Message`

        契约：
        - 输入：无
        - 输出：`Message`
        - 副作用：触发抓取
        - 失败语义：抓取失败时抛 `ValueError`
        """
        url_contents = self.fetch_url_contents()
        return Message(text="\n\n".join([x["text"] for x in url_contents]), data={"data": url_contents})
