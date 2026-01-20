"""
模块名称：Tavily Search 组件

本模块封装 Tavily Search API 的调用与结果解析逻辑，用于为 RAG/LLM 提供检索结果。
主要功能：
- 组装搜索请求并调用 Tavily API；
- 解析检索结果并输出为 Data/DataFrame。

关键组件：
- TavilySearchComponent：搜索组件入口。

设计背景：为 LLM/RAG 提供稳定的搜索能力与结构化输出。
注意事项：依赖外部 API，建议设置合理的查询参数以控制成本与延迟。
"""

import httpx

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import BoolInput, DropdownInput, IntInput, MessageTextInput, SecretStrInput
from lfx.log.logger import logger
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame
from lfx.template.field.base import Output


class TavilySearchComponent(Component):
    """Tavily Search 检索组件

    契约：输入 `api_key` 与 `query`；输出 `list[Data]` 或 `DataFrame`。
    关键路径：1) 解析查询与过滤条件 2) 调用 Search API 3) 解析结果输出。
    决策：使用 Tavily Search API 作为检索来源
    问题：需要面向 LLM 的高相关搜索结果
    方案：调用官方 Search 接口并解析字段
    代价：依赖外部服务与 API 配额
    重评：当需要替换为内部搜索服务时
    """
    display_name = "Tavily Search API"
    description = """**Tavily Search** is a search engine optimized for LLMs and RAG, \
        aimed at efficient, quick, and persistent search results."""
    icon = "TavilyIcon"

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
            tool_mode=True,
        ),
        DropdownInput(
            name="search_depth",
            display_name="Search Depth",
            info="The depth of the search.",
            options=["basic", "advanced"],
            value="advanced",
            advanced=True,
        ),
        IntInput(
            name="chunks_per_source",
            display_name="Chunks Per Source",
            info=("The number of content chunks to retrieve from each source (1-3). Only works with advanced search."),
            value=3,
            advanced=True,
        ),
        DropdownInput(
            name="topic",
            display_name="Search Topic",
            info="The category of the search.",
            options=["general", "news"],
            value="general",
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
            options=["day", "week", "month", "year"],
            value=None,  # 注意：默认 None 表示可选参数。
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

    outputs = [
        Output(display_name="DataFrame", name="dataframe", method="fetch_content_dataframe"),
    ]

    def fetch_content(self) -> list[Data]:
        """执行搜索并返回 Data 列表

        契约：返回 `list[Data]`；失败时返回包含错误信息的 Data。
        关键路径（三步）：
        1) 解析域名过滤与高级参数
        2) 组装并发送搜索请求
        3) 解析响应并封装结果
        异常流：超时/HTTP 错误/请求错误/解析错误返回错误 Data。
        排障入口：日志包含 `Request timed out` / `HTTP error occurred` / `Request error occurred`。
        决策：超时设置为 90s
        问题：搜索接口在复杂查询下响应较慢
        方案：显式设置 90s 超时
        代价：等待时间更长
        重评：当接口响应时间稳定后
        """
        try:
            # 实现：仅在提供域名时才进行解析。
            include_domains = None
            exclude_domains = None

            if self.include_domains:
                include_domains = [domain.strip() for domain in self.include_domains.split(",") if domain.strip()]

            if self.exclude_domains:
                exclude_domains = [domain.strip() for domain in self.exclude_domains.split(",") if domain.strip()]

            url = "https://api.tavily.com/search"
            headers = {
                "content-type": "application/json",
                "accept": "application/json",
            }

            payload = {
                "api_key": self.api_key,
                "query": self.query,
                "search_depth": self.search_depth,
                "topic": self.topic,
                "max_results": self.max_results,
                "include_images": self.include_images,
                "include_answer": self.include_answer,
                "include_raw_content": self.include_raw_content,
                "days": self.days,
                "time_range": self.time_range,
            }

            # 注意：仅在域名列表存在时加入 payload。
            if include_domains:
                payload["include_domains"] = include_domains
            if exclude_domains:
                payload["exclude_domains"] = exclude_domains

            # 注意：仅在高级搜索时允许 chunks_per_source。
            if self.search_depth == "advanced" and self.chunks_per_source:
                payload["chunks_per_source"] = self.chunks_per_source

            if self.topic == "news" and self.days:
                # 注意：news 场景下保证 days 为整数。
                payload["days"] = int(self.days)

            # 注意：仅在设置了 time_range 时追加。
            if hasattr(self, "time_range") and self.time_range:
                payload["time_range"] = self.time_range

            # 实现：设置 90s 超时避免请求挂起。
            with httpx.Client(timeout=90.0) as client:
                response = client.post(url, json=payload, headers=headers)

            response.raise_for_status()
            search_results = response.json()

            data_results = []

            if self.include_answer and search_results.get("answer"):
                data_results.append(Data(text=search_results["answer"]))

            for result in search_results.get("results", []):
                content = result.get("content", "")
                result_data = {
                    "title": result.get("title"),
                    "url": result.get("url"),
                    "content": content,
                    "score": result.get("score"),
                }
                if self.include_raw_content:
                    result_data["raw_content"] = result.get("raw_content")

                data_results.append(Data(text=content, data=result_data))

            if self.include_images and search_results.get("images"):
                data_results.append(Data(text="Images found", data={"images": search_results["images"]}))

        except httpx.TimeoutException:
            error_message = "Request timed out (90s). Please try again or adjust parameters."
            logger.error(error_message)
            return [Data(text=error_message, data={"error": error_message})]
        except httpx.HTTPStatusError as exc:
            error_message = f"HTTP error occurred: {exc.response.status_code} - {exc.response.text}"
            logger.error(error_message)
            return [Data(text=error_message, data={"error": error_message})]
        except httpx.RequestError as exc:
            error_message = f"Request error occurred: {exc}"
            logger.error(error_message)
            return [Data(text=error_message, data={"error": error_message})]
        except ValueError as exc:
            error_message = f"Invalid response format: {exc}"
            logger.error(error_message)
            return [Data(text=error_message, data={"error": error_message})]
        else:
            self.status = data_results
            return data_results

    def fetch_content_dataframe(self) -> DataFrame:
        """将搜索结果转换为 DataFrame。"""
        data = self.fetch_content()
        return DataFrame(data)
