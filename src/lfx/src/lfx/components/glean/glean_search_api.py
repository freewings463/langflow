"""
模块名称：`Glean` 搜索 API 组件

本模块提供 `GleanSearchAPIComponent` 与 `GleanAPIWrapper`，用于调用 `Glean` 搜索接口并
将结果转换为 `Data`/`DataFrame`。主要功能包括：
- 构建并发送 `Glean` 搜索请求
- 处理结果并补齐 `snippets` 字段
- 暴露 LangChain 工具与组件输出

关键组件：`GleanAPIWrapper`、`GleanSearchAPIComponent`
设计背景：统一 `Glean` 搜索能力接入与结果结构化
注意事项：请求需携带 `Bearer` token 与 `X-Scio-ActAs` 头；无结果时抛断言错误
"""

import json
from typing import Any
from urllib.parse import urljoin

import httpx
from langchain_core.tools import StructuredTool, ToolException
from pydantic import BaseModel
from pydantic.v1 import Field

from lfx.base.langchain_utilities.model import LCToolComponent
from lfx.field_typing import Tool
from lfx.inputs.inputs import IntInput, MultilineInput, NestedDictInput, SecretStrInput, StrInput
from lfx.io import Output
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame


class GleanSearchAPISchema(BaseModel):
    query: str = Field(..., description="The search query")
    page_size: int = Field(10, description="Maximum number of results to return")
    request_options: dict[str, Any] | None = Field(default_factory=dict, description="Request Options")


class GleanAPIWrapper(BaseModel):
    """`Glean` API 调用封装。
    契约：提供搜索请求构建与执行方法；返回结果列表。
    关键路径：组装请求 → 发送 HTTP → 解析结果。
    决策：使用 `httpx.post` 同步请求。问题：组件执行路径需要同步语义；方案：同步调用；代价：阻塞线程；重评：当需要异步 I/O 时。
    """

    glean_api_url: str
    glean_access_token: str
    act_as: str = "langflow-component@datastax.com"  # TODO：自动检测 `act_as` 标识

    def _prepare_request(
        self,
        query: str,
        page_size: int = 10,
        request_options: dict[str, Any] | None = None,
    ) -> dict:
        """构建请求参数。
        契约：返回包含 `url`、`headers`、`payload` 的 dict。
        关键路径：规范化 base_url → 拼接 search 路径 → 组装 headers/payload。
        决策：自动补齐尾部 `/`。问题：避免 urljoin 覆盖路径；方案：强制补斜杠；代价：对非标准 URL 可能误判；重评：当 URL 处理策略统一时。
        """
        # 注意：确保 base_url 以 `/` 结尾，避免 urljoin 覆盖路径。
        url = self.glean_api_url
        if not url.endswith("/"):
            url += "/"

        return {
            "url": urljoin(url, "search"),
            "headers": {
                "Authorization": f"Bearer {self.glean_access_token}",
                "X-Scio-ActAs": self.act_as,
            },
            "payload": {
                "query": query,
                "pageSize": page_size,
                "requestOptions": request_options,
            },
        }

    def results(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        """获取搜索结果列表。
        契约：返回结果列表；空结果抛 `AssertionError`。
        关键路径：调用 `_search_api_results` → 空结果校验。
        决策：空结果视为失败。问题：下游依赖至少一条结果；方案：抛错；代价：无法表示空搜索；重评：当允许空结果时。
        """
        results = self._search_api_results(query, **kwargs)

        if len(results) == 0:
            msg = "No good Glean Search Result was found"
            raise AssertionError(msg)

        return results

    def run(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        """执行搜索并做结果规范化。
        契约：返回规范化后的结果列表；异常统一为 `ToolException`。
        关键路径：获取结果 → 补齐 `snippets` → 返回。
        决策：缺少 snippets 时用 title 兜底。问题：下游依赖 text 展示；方案：title 兜底；代价：语义可能不准确；重评：当 API 返回稳定 snippets 时。
        """
        try:
            results = self.results(query, **kwargs)

            processed_results = []
            for result in results:
                if "title" in result:
                    result["snippets"] = result.get("snippets", [{"snippet": {"text": result["title"]}}])
                    if "text" not in result["snippets"][0]:
                        result["snippets"][0]["text"] = result["title"]

                processed_results.append(result)
        except Exception as e:
            error_message = f"Error in Glean Search API: {e!s}"
            raise ToolException(error_message) from e

        return processed_results

    def _search_api_results(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        """调用 `Glean` 搜索接口并返回原始结果。
        契约：HTTP 非 2xx 会抛异常；成功返回 results 列表。
        关键路径：构建请求 → POST → 解析 JSON。
        决策：使用 `response.raise_for_status` 强失败。问题：避免静默失败；方案：直接抛错；代价：调用方需处理异常；重评：当需要软失败时。
        """
        request_details = self._prepare_request(query, **kwargs)

        response = httpx.post(
            request_details["url"],
            json=request_details["payload"],
            headers=request_details["headers"],
        )

        response.raise_for_status()
        response_json = response.json()

        return response_json.get("results", [])

    @staticmethod
    def _result_as_string(result: dict) -> str:
        """将结果转为格式化 JSON 字符串。
        契约：返回可读的 JSON 字符串。
        关键路径：`json.dumps` 格式化输出。
        决策：使用缩进便于调试。问题：可读性需求；方案：indent=4；代价：字符串更长；重评：当需要压缩输出时。
        """
        return json.dumps(result, indent=4)


class GleanSearchAPIComponent(LCToolComponent):
    """`Glean` 搜索组件。
    契约：输入为 API URL/Token 与查询；输出为工具或 `DataFrame`。
    关键路径：构建 wrapper → 生成工具 → 运行查询 → 结构化输出。
    决策：以 `StructuredTool` 暴露搜索能力。问题：统一工具接口；方案：LangChain 工具；代价：参数需满足 schema；重评：当不再依赖 LangChain 时。
    """

    display_name: str = "Glean Search API"
    description: str = "Search using Glean's API."
    documentation: str = "https://docs.langflow.org/bundles-glean"
    icon: str = "Glean"

    outputs = [
        Output(display_name="DataFrame", name="dataframe", method="fetch_content_dataframe"),
    ]

    inputs = [
        StrInput(name="glean_api_url", display_name="Glean API URL", required=True),
        SecretStrInput(name="glean_access_token", display_name="Glean Access Token", required=True),
        MultilineInput(name="query", display_name="Query", required=True, tool_mode=True),
        IntInput(name="page_size", display_name="Page Size", value=10),
        NestedDictInput(name="request_options", display_name="Request Options", required=False),
    ]

    def build_tool(self) -> Tool:
        """构建 `Glean` 搜索工具。
        契约：返回 `StructuredTool`；失败抛异常。
        关键路径：构建 wrapper → from_function 生成工具 → 写入状态。
        决策：工具名固定为 `glean_search_api`。问题：确保工具唯一且可识别；方案：固定命名；代价：多实例冲突；重评：当需要多实例区分时。
        """
        wrapper = self._build_wrapper(
            glean_api_url=self.glean_api_url,
            glean_access_token=self.glean_access_token,
        )

        tool = StructuredTool.from_function(
            name="glean_search_api",
            description="Search Glean for relevant results.",
            func=wrapper.run,
            args_schema=GleanSearchAPISchema,
        )

        self.status = "Glean Search API Tool for Langchain"

        return tool

    def run_model(self) -> DataFrame:
        """运行组件并返回 `DataFrame`。
        契约：等价于 `fetch_content_dataframe`。
        关键路径：调用 `fetch_content_dataframe`。
        决策：复用 DataFrame 输出路径。问题：保持组件输出一致；方案：直接委托；代价：无额外控制；重评：当需要不同输出模式时。
        """
        return self.fetch_content_dataframe()

    def fetch_content(self) -> list[Data]:
        """执行搜索并返回 `Data` 列表。
        契约：返回结果 `Data` 列表；失败抛异常。
        关键路径：构建工具 → 运行查询 → 组装 `Data`。
        决策：使用 `snippets[0].text` 作为 `Data.text`。问题：需要摘要文本；方案：首片段；代价：可能不是最相关片段；重评：当 `snippets` 结构变化时。
        """
        tool = self.build_tool()

        results = tool.run(
            {
                "query": self.query,
                "page_size": self.page_size,
                "request_options": self.request_options,
            }
        )

        # 注意：将结果列表映射为 `Data` 对象。
        data = [Data(data=result, text=result["snippets"][0]["text"]) for result in results]
        self.status = data  # type: ignore[assignment]

        return data

    def _build_wrapper(
        self,
        glean_api_url: str,
        glean_access_token: str,
    ):
        """构建 `GleanAPIWrapper` 实例。
        契约：返回 wrapper 实例。
        关键路径：透传 URL/Token 参数。
        决策：不在此处校验 token。问题：校验可能需远端；方案：执行时失败；代价：晚失败；重评：当需要提前校验时。
        """
        return GleanAPIWrapper(
            glean_api_url=glean_api_url,
            glean_access_token=glean_access_token,
        )

    def fetch_content_dataframe(self) -> DataFrame:
        """将搜索结果转换为 `DataFrame`。
        契约：返回 `DataFrame`，内容来自 `fetch_content`。
        关键路径：获取 `Data` → 构建 `DataFrame`。
        决策：保持与 `fetch_content` 一致的数据来源。问题：避免重复请求；方案：复用；代价：受 `fetch_content` 结果限制；重评：当需要不同列结构时。
        """
        data = self.fetch_content()
        return DataFrame(data)
