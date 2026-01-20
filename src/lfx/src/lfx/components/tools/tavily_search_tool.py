"""
模块名称：`Tavily` 搜索工具组件

本模块封装 Tavily 搜索 API，支持高级检索与内容片段返回。
主要功能包括：
- 校验搜索参数并转换为枚举值
- 调用 Tavily API 并结构化结果
- 可选返回摘要、图片与原始内容

关键组件：
- `TavilySearchToolComponent.run_model`：参数校验与调用入口
- `TavilySearchToolComponent._tavily_search`：HTTP 请求与结果整理

设计背景：为 RAG 场景提供可配置的实时搜索能力。
注意事项：请求超时或配额限制会抛 `ToolException`。
"""

from enum import Enum

import httpx
from langchain.tools import StructuredTool
from langchain_core.tools import ToolException
from pydantic import BaseModel, Field

from lfx.base.langchain_utilities.model import LCToolComponent
from lfx.field_typing import Tool
from lfx.inputs.inputs import BoolInput, DropdownInput, IntInput, MessageTextInput, SecretStrInput
from lfx.log.logger import logger
from lfx.schema.data import Data

# 注意：限制单源返回的内容片段数，避免过长输出。
MAX_CHUNKS_PER_SOURCE = 3


class TavilySearchDepth(Enum):
    BASIC = "basic"
    ADVANCED = "advanced"


class TavilySearchTopic(Enum):
    GENERAL = "general"
    NEWS = "news"


class TavilySearchTimeRange(Enum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"


class TavilySearchSchema(BaseModel):
    """Tavily 搜索参数结构。"""
    query: str = Field(..., description="The search query you want to execute with Tavily.")
    search_depth: TavilySearchDepth = Field(TavilySearchDepth.BASIC, description="The depth of the search.")
    topic: TavilySearchTopic = Field(TavilySearchTopic.GENERAL, description="The category of the search.")
    max_results: int = Field(5, description="The maximum number of search results to return.")
    include_images: bool = Field(default=False, description="Include a list of query-related images in the response.")
    include_answer: bool = Field(default=False, description="Include a short answer to original query.")
    chunks_per_source: int = Field(
        default=MAX_CHUNKS_PER_SOURCE,
        description=(
            "The number of content chunks to retrieve from each source (max 500 chars each). Only for advanced search."
        ),
        ge=1,
        le=MAX_CHUNKS_PER_SOURCE,
    )
    include_domains: list[str] = Field(
        default=[],
        description="A list of domains to specifically include in the search results.",
    )
    exclude_domains: list[str] = Field(
        default=[],
        description="A list of domains to specifically exclude from the search results.",
    )
    include_raw_content: bool = Field(
        default=False,
        description="Include the cleaned and parsed HTML content of each search result.",
    )
    days: int = Field(
        default=7,
        description="Number of days back from the current date to include. Only available if topic is news.",
        ge=1,
    )
    time_range: TavilySearchTimeRange | None = Field(
        default=None,
        description="The time range back from the current date to filter results.",
    )


class TavilySearchToolComponent(LCToolComponent):
    """Tavily 搜索工具组件。

    契约：输入查询与搜索参数，输出 `Data` 列表。
    决策：在组件层进行参数校验与枚举转换。
    问题：前端传入字符串易造成枚举不一致。
    方案：统一在 `run_model` 中转换并捕获错误。
    代价：增加组件层处理逻辑。
    重评：当输入类型强校验由前端保证时简化转换。
    """
    display_name = "Tavily Search API"
    description = """**Tavily Search API** is a search engine optimized for LLMs and RAG, \
        aimed at efficient, quick, and persistent search results. It can be used independently or as an agent tool.

Note: Check 'Advanced' for all options.
"""
    icon = "TavilyIcon"
    name = "TavilyAISearch"
    documentation = "https://docs.tavily.com/"
    legacy = True
    replacement = ["tavily.TavilySearchComponent"]

    inputs = [
        SecretStrInput(
            name="api_key",
            display_name="Tavily API Key",
            required=True,
            info="Your Tavily API Key.",
        ),
        MessageTextInput(
            name="query",
            display_name="Search Query",
            info="The search query you want to execute with Tavily.",
        ),
        DropdownInput(
            name="search_depth",
            display_name="Search Depth",
            info="The depth of the search.",
            options=list(TavilySearchDepth),
            value=TavilySearchDepth.ADVANCED,
            advanced=True,
        ),
        IntInput(
            name="chunks_per_source",
            display_name="Chunks Per Source",
            info=("The number of content chunks to retrieve from each source (1-3). Only works with advanced search."),
            value=MAX_CHUNKS_PER_SOURCE,
            advanced=True,
        ),
        DropdownInput(
            name="topic",
            display_name="Search Topic",
            info="The category of the search.",
            options=list(TavilySearchTopic),
            value=TavilySearchTopic.GENERAL,
            advanced=True,
        ),
        IntInput(
            name="days",
            display_name="Days",
            info="Number of days back from current date to include. Only available with news topic.",
            value=7,
            advanced=True,
        ),
        IntInput(
            name="max_results",
            display_name="Max Results",
            info="The maximum number of search results to return.",
            value=5,
            advanced=True,
        ),
        BoolInput(
            name="include_answer",
            display_name="Include Answer",
            info="Include a short answer to original query.",
            value=True,
            advanced=True,
        ),
        DropdownInput(
            name="time_range",
            display_name="Time Range",
            info="The time range back from the current date to filter results.",
            options=list(TavilySearchTimeRange),
            value=None,
            advanced=True,
        ),
        BoolInput(
            name="include_images",
            display_name="Include Images",
            info="Include a list of query-related images in the response.",
            value=True,
            advanced=True,
        ),
        MessageTextInput(
            name="include_domains",
            display_name="Include Domains",
            info="Comma-separated list of domains to include in the search results.",
            advanced=True,
        ),
        MessageTextInput(
            name="exclude_domains",
            display_name="Exclude Domains",
            info="Comma-separated list of domains to exclude from the search results.",
            advanced=True,
        ),
        BoolInput(
            name="include_raw_content",
            display_name="Include Raw Content",
            info="Include the cleaned and parsed HTML content of each search result.",
            value=False,
            advanced=True,
        ),
    ]

    def run_model(self) -> list[Data]:
        """校验参数并执行 Tavily 搜索。

        关键路径（三步）：
        1) 将输入转换为枚举并校验
        2) 解析域名白名单/黑名单
        3) 调用 `_tavily_search` 并返回结果
        异常流：枚举解析失败返回带 `error` 的 `Data`。
        """
        # 注意：将字符串转换为枚举，确保 API 传参一致。
        try:
            search_depth_enum = (
                self.search_depth
                if isinstance(self.search_depth, TavilySearchDepth)
                else TavilySearchDepth(str(self.search_depth).lower())
            )
        except ValueError as e:
            error_message = f"Invalid search depth value: {e!s}"
            self.status = error_message
            return [Data(data={"error": error_message})]

        try:
            topic_enum = (
                self.topic if isinstance(self.topic, TavilySearchTopic) else TavilySearchTopic(str(self.topic).lower())
            )
        except ValueError as e:
            error_message = f"Invalid topic value: {e!s}"
            self.status = error_message
            return [Data(data={"error": error_message})]

        try:
            time_range_enum = (
                self.time_range
                if isinstance(self.time_range, TavilySearchTimeRange)
                else TavilySearchTimeRange(str(self.time_range).lower())
                if self.time_range
                else None
            )
        except ValueError as e:
            error_message = f"Invalid time range value: {e!s}"
            self.status = error_message
            return [Data(data={"error": error_message})]

        # 注意：初始化域名过滤变量为 None，便于按需注入。
        include_domains = None
        exclude_domains = None

        # 注意：仅在提供域名时才解析，避免空串影响。
        if self.include_domains:
            include_domains = [domain.strip() for domain in self.include_domains.split(",") if domain.strip()]

        if self.exclude_domains:
            exclude_domains = [domain.strip() for domain in self.exclude_domains.split(",") if domain.strip()]

        return self._tavily_search(
            self.query,
            search_depth=search_depth_enum,
            topic=topic_enum,
            max_results=self.max_results,
            include_images=self.include_images,
            include_answer=self.include_answer,
            chunks_per_source=self.chunks_per_source,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            include_raw_content=self.include_raw_content,
            days=self.days,
            time_range=time_range_enum,
        )

    def build_tool(self) -> Tool:
        """构建可被 LangChain 调用的搜索工具。"""
        return StructuredTool.from_function(
            name="tavily_search",
            description="Perform a web search using the Tavily API.",
            func=self._tavily_search,
            args_schema=TavilySearchSchema,
        )

    def _tavily_search(
        self,
        query: str,
        *,
        search_depth: TavilySearchDepth = TavilySearchDepth.BASIC,
        topic: TavilySearchTopic = TavilySearchTopic.GENERAL,
        max_results: int = 5,
        include_images: bool = False,
        include_answer: bool = False,
        chunks_per_source: int = MAX_CHUNKS_PER_SOURCE,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        include_raw_content: bool = False,
        days: int = 7,
        time_range: TavilySearchTimeRange | None = None,
    ) -> list[Data]:
        """调用 Tavily API 并返回结构化结果。

        关键路径（三步）：
        1) 校验枚举与数值边界
        2) 构造请求体并发送 HTTP 请求
        3) 解析响应并转换为 `Data`
        异常流：超时、HTTP 错误会抛 `ToolException`。
        """
        # 注意：枚举必须是已知类型。
        if not isinstance(search_depth, TavilySearchDepth):
            msg = f"Invalid search_depth value: {search_depth}"
            raise TypeError(msg)
        if not isinstance(topic, TavilySearchTopic):
            msg = f"Invalid topic value: {topic}"
            raise TypeError(msg)

        # 注意：限制单源片段数，避免超长输出。
        if not 1 <= chunks_per_source <= MAX_CHUNKS_PER_SOURCE:
            msg = f"chunks_per_source must be between 1 and {MAX_CHUNKS_PER_SOURCE}, got {chunks_per_source}"
            raise ValueError(msg)

        # 注意：新闻检索的时间范围需为正数。
        if days < 1:
            msg = f"days must be greater than or equal to 1, got {days}"
            raise ValueError(msg)

        try:
            url = "https://api.tavily.com/search"
            headers = {
                "content-type": "application/json",
                "accept": "application/json",
            }
            payload = {
                "api_key": self.api_key,
                "query": query,
                "search_depth": search_depth.value,
                "topic": topic.value,
                "max_results": max_results,
                "include_images": include_images,
                "include_answer": include_answer,
                "chunks_per_source": chunks_per_source if search_depth == TavilySearchDepth.ADVANCED else None,
                "include_domains": include_domains if include_domains else None,
                "exclude_domains": exclude_domains if exclude_domains else None,
                "include_raw_content": include_raw_content,
                "days": days if topic == TavilySearchTopic.NEWS else None,
                "time_range": time_range.value if time_range else None,
            }

            with httpx.Client(timeout=90.0) as client:
                response = client.post(url, json=payload, headers=headers)

            response.raise_for_status()
            search_results = response.json()

            data_results = [
                Data(
                    data={
                        "title": result.get("title"),
                        "url": result.get("url"),
                        "content": result.get("content"),
                        "score": result.get("score"),
                        "raw_content": result.get("raw_content") if include_raw_content else None,
                    }
                )
                for result in search_results.get("results", [])
            ]

            if include_answer and search_results.get("answer"):
                data_results.insert(0, Data(data={"answer": search_results["answer"]}))

            if include_images and search_results.get("images"):
                data_results.append(Data(data={"images": search_results["images"]}))

            self.status = data_results  # type: ignore[assignment]

        except httpx.TimeoutException as e:
            error_message = "Request timed out (90s). Please try again or adjust parameters."
            logger.error(f"Timeout error: {e}")
            self.status = error_message
            raise ToolException(error_message) from e
        except httpx.HTTPStatusError as e:
            error_message = f"HTTP error: {e.response.status_code} - {e.response.text}"
            logger.debug(error_message)
            self.status = error_message
            raise ToolException(error_message) from e
        except Exception as e:
            error_message = f"Unexpected error: {e}"
            logger.debug("Error running Tavily Search", exc_info=True)
            self.status = error_message
            raise ToolException(error_message) from e
        return data_results
