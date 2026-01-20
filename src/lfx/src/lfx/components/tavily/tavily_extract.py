"""
模块名称：Tavily Extract 组件

本模块封装 Tavily Extract API 的调用与结果解析逻辑，用于从 URL 列表抽取原始内容。
主要功能：
- 组装 Extract 请求并调用 Tavily API；
- 将抽取结果转换为 Data/DataFrame 输出。

关键组件：
- TavilyExtractComponent：抽取组件入口。

设计背景：提供统一的外部内容抽取能力，便于接入搜索与 RAG 流程。
注意事项：依赖外部 API，需提供有效 `api_key` 并注意请求超时。
"""

import httpx

from lfx.custom import Component
from lfx.io import BoolInput, DropdownInput, MessageTextInput, Output, SecretStrInput
from lfx.log.logger import logger
from lfx.schema import Data
from lfx.schema.dataframe import DataFrame


class TavilyExtractComponent(Component):
    """Tavily Extract 抽取组件

    契约：输入 `api_key` 与 `urls`；输出 `list[Data]` 或 `DataFrame`。
    关键路径：1) 解析 URL 列表 2) 调用 Extract API 3) 组装结果数据。
    决策：使用 Tavily Extract API 作为内容抽取来源
    问题：需要稳定的网页内容抽取能力
    方案：调用官方 Extract 接口并解析结果
    代价：依赖外部服务与网络可用性
    重评：当需要离线抽取或自建抽取服务时
    """

    display_name = "Tavily Extract API"
    description = """**Tavily Extract** extract raw content from URLs."""
    icon = "TavilyIcon"

    inputs = [
        SecretStrInput(
            name="api_key",
            display_name="Tavily API Key",
            required=True,
            info="Your Tavily API Key.",
        ),
        MessageTextInput(
            name="urls",
            display_name="URLs",
            info="Comma-separated list of URLs to extract content from.",
            required=True,
        ),
        DropdownInput(
            name="extract_depth",
            display_name="Extract Depth",
            info="The depth of the extraction process.",
            options=["basic", "advanced"],
            value="basic",
            advanced=True,
        ),
        BoolInput(
            name="include_images",
            display_name="Include Images",
            info="Include a list of images extracted from the URLs.",
            value=False,
            advanced=True,
        ),
    ]

    outputs = [
        Output(display_name="DataFrame", name="dataframe", method="fetch_content"),
    ]

    def run_model(self) -> DataFrame:
        """兼容父类调用入口，返回 DataFrame 输出。"""
        return self.fetch_content_dataframe()

    def fetch_content(self) -> list[Data]:
        """执行内容抽取并返回 Data 列表

        契约：返回 `list[Data]`；失败时返回包含错误信息的 Data。
        关键路径（三步）：
        1) 解析并清理 URL 列表
        2) 构建请求并调用 Extract API
        3) 解析响应并封装为 Data
        异常流：超时/HTTP 错误/解析错误返回错误 Data。
        排障入口：日志包含 `Request timed out` / `HTTP error occurred` / `Data processing error`。
        决策：超时设置为 90s
        问题：抽取接口可能响应较慢
        方案：显式设置 90s 超时
        代价：等待时间更长，占用连接
        重评：当响应延迟稳定下降时
        """
        try:
            # 实现：按逗号拆分并过滤空 URL。
            urls = [url.strip() for url in (self.urls or "").split(",") if url.strip()]
            if not urls:
                error_message = "No valid URLs provided"
                logger.error(error_message)
                return [Data(text=error_message, data={"error": error_message})]

            url = "https://api.tavily.com/extract"
            headers = {
                "content-type": "application/json",
                "accept": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            }
            payload = {
                "urls": urls,
                "extract_depth": self.extract_depth,
                "include_images": self.include_images,
            }

            with httpx.Client(timeout=90.0) as client:
                response = client.post(url, json=payload, headers=headers)
                response.raise_for_status()

        except httpx.TimeoutException as exc:
            error_message = f"Request timed out (90s): {exc}"
            logger.error(error_message)
            return [Data(text=error_message, data={"error": error_message})]
        except httpx.HTTPStatusError as exc:
            error_message = f"HTTP error occurred: {exc.response.status_code} - {exc.response.text}"
            logger.error(error_message)
            return [Data(text=error_message, data={"error": error_message})]
        except (ValueError, KeyError, AttributeError, httpx.RequestError) as exc:
            error_message = f"Data processing error: {exc}"
            logger.error(error_message)
            return [Data(text=error_message, data={"error": error_message})]
        else:
            extract_results = response.json()
            data_results = []

            # 实现：处理成功的抽取结果。
            for result in extract_results.get("results", []):
                raw_content = result.get("raw_content", "")
                images = result.get("images", [])
                result_data = {"url": result.get("url"), "raw_content": raw_content, "images": images}
                data_results.append(Data(text=raw_content, data=result_data))

            # 注意：失败结果保留用于排障与补偿处理。
            if extract_results.get("failed_results"):
                data_results.append(
                    Data(
                        text="Failed extractions",
                        data={"failed_results": extract_results["failed_results"]},
                    )
                )

            self.status = data_results
            return data_results

    def fetch_content_dataframe(self) -> DataFrame:
        """将抽取结果转换为 DataFrame。"""
        data = self.fetch_content()
        return DataFrame(data)
