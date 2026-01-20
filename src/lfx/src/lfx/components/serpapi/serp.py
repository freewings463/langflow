"""模块名称：SerpAPI 搜索组件

本模块封装 SerpAPI 搜索能力，并提供结果裁剪与格式化输出。
主要功能包括：构建 SerpAPI wrapper、执行查询、限制结果数量与片段长度。

关键组件：
- `SerpAPISchema`：搜索参数的结构化定义
- `SerpComponent`：SerpAPI 组件入口

设计背景：在 Langflow 中统一外部搜索引擎调用与结果约束方式。
注意事项：API Key 必填；结果截断策略会影响召回完整性。
"""

from typing import Any

from langchain_community.utilities.serpapi import SerpAPIWrapper
from langchain_core.tools import ToolException
from pydantic import BaseModel, Field

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import DictInput, IntInput, MultilineInput, SecretStrInput
from lfx.io import Output
from lfx.log.logger import logger
from lfx.schema.data import Data
from lfx.schema.message import Message


class SerpAPISchema(BaseModel):
    """SerpAPI 搜索参数结构。

    契约：输入 `query/params/max_results/max_snippet_length`；输出校验后的参数模型；
    副作用无；失败语义：字段缺失或类型不匹配时抛校验异常。
    关键路径：1) 校验必填 `query` 2) 合并默认参数 3) 输出结构化配置。
    决策：默认使用 `google` 引擎与 `us/en` 区域参数
    问题：需要稳定的默认搜索环境
    方案：在 `params` 默认值中固定 `engine/gl/hl`
    代价：地域与语言覆盖范围受限
    重评：当引入多区域配置时改为显式输入
    """

    query: str = Field(..., description="The search query")
    params: dict[str, Any] | None = Field(
        default={
            "engine": "google",
            "google_domain": "google.com",
            "gl": "us",
            "hl": "en",
        },
        description="Additional search parameters",
    )
    max_results: int = Field(5, description="Maximum number of results to return")
    max_snippet_length: int = Field(100, description="Maximum length of each result snippet")


class SerpComponent(Component):
    """SerpAPI 搜索组件。

    契约：输入 `serpapi_api_key/input_value/search_params/max_results/max_snippet_length`；
    输出 `list[Data]` 或 `Message`；副作用：调用外部 SerpAPI；失败语义：搜索失败抛 `ToolException`。
    关键路径：1) 构建 wrapper 2) 执行搜索并裁剪结果 3) 输出数据或文本。
    决策：对标题与摘要做长度裁剪
    问题：控制 UI 展示与下游 token 成本
    方案：使用 `max_results` 与 `max_snippet_length` 限制
    代价：可能丢失重要信息
    重评：当下游需要完整内容时提供关闭裁剪选项
    """
    display_name = "Serp Search API"
    description = "Call Serp Search API with result limiting"
    name = "Serp"
    icon = "SerpSearch"

    inputs = [
        SecretStrInput(name="serpapi_api_key", display_name="SerpAPI API Key", required=True),
        MultilineInput(
            name="input_value",
            display_name="Input",
            tool_mode=True,
        ),
        DictInput(name="search_params", display_name="Parameters", advanced=True, is_list=True),
        IntInput(name="max_results", display_name="Max Results", value=5, advanced=True),
        IntInput(name="max_snippet_length", display_name="Max Snippet Length", value=100, advanced=True),
    ]

    outputs = [
        Output(display_name="Data", name="data", method="fetch_content"),
        Output(display_name="Text", name="text", method="fetch_content_text"),
    ]

    def _build_wrapper(self, params: dict[str, Any] | None = None) -> SerpAPIWrapper:
        """构建 SerpAPIWrapper。

        契约：输入可选 `params`；输出 `SerpAPIWrapper`；副作用无；
        失败语义：API Key 缺失将由 SerpAPIWrapper 内部抛错。
        关键路径：1) 合并参数 2) 初始化 wrapper。
        决策：仅在传入参数时覆盖默认参数
        问题：避免覆盖默认配置导致搜索行为变化
        方案：传入参数时创建新的 wrapper
        代价：多次构建 wrapper 有轻微开销
        重评：当 wrapper 支持动态更新参数时复用实例
        """
        params = params or {}
        if params:
            return SerpAPIWrapper(
                serpapi_api_key=self.serpapi_api_key,
                params=params,
            )
        return SerpAPIWrapper(serpapi_api_key=self.serpapi_api_key)

    def run_model(self) -> list[Data]:
        """组件默认执行入口，返回搜索数据列表。

        契约：输入无；输出 `list[Data]`；副作用：调用外部 API；
        失败语义：搜索失败抛 `ToolException`。
        关键路径：1) 复用 `fetch_content`。
        决策：将默认执行与 `fetch_content` 对齐
        问题：减少重复逻辑
        方案：直接调用 `fetch_content`
        代价：无
        重评：当需要不同默认输出时改为独立实现
        """
        return self.fetch_content()

    def fetch_content(self) -> list[Data]:
        """执行搜索并返回结构化结果列表。

        关键路径（三步）：
        1) 构建或复用 SerpAPI wrapper
        2) 调用 API 获取 `organic_results`
        3) 裁剪并转换为 `Data`

        异常流：任何 API/解析异常会包装为 `ToolException`。
        性能瓶颈：网络调用与结果解析。
        排障入口：日志关键字 `Error in SerpAPI search`。
        决策：仅返回 `organic_results`
        问题：付费接口结果类型繁多且噪声高
        方案：限定主结果列表
        代价：其他结果（如广告/知识图谱）被忽略
        重评：当需要更丰富结果时开放结果类型选择
        """
        wrapper = self._build_wrapper(self.search_params)

        def search_func(
            query: str, params: dict[str, Any] | None = None, max_results: int = 5, max_snippet_length: int = 100
        ) -> list[Data]:
            """执行一次 SerpAPI 查询并裁剪结果。

            契约：输入 `query/params/max_results/max_snippet_length`；输出 `list[Data]`；
            副作用：调用外部 API；失败语义：异常包装为 `ToolException`。
            关键路径：1) 选择 wrapper 2) 获取 `organic_results` 3) 裁剪并映射为 Data。
            决策：按 `max_results` 截断结果
            问题：避免过多结果造成下游负担
            方案：切片 `[:max_results]`
            代价：长尾结果被丢弃
            重评：当下游支持分页时改为分页输出
            """
            try:
                local_wrapper = wrapper
                if params:
                    local_wrapper = self._build_wrapper(params)

                full_results = local_wrapper.results(query)
                organic_results = full_results.get("organic_results", [])[:max_results]

                limited_results = [
                    Data(
                        text=result.get("snippet", ""),
                        data={
                            "title": result.get("title", "")[:max_snippet_length],
                            "link": result.get("link", ""),
                            "snippet": result.get("snippet", "")[:max_snippet_length],
                        },
                    )
                    for result in organic_results
                ]

            except Exception as e:
                error_message = f"Error in SerpAPI search: {e!s}"
                logger.debug(error_message)
                raise ToolException(error_message) from e
            return limited_results

        results = search_func(
            self.input_value,
            params=self.search_params,
            max_results=self.max_results,
            max_snippet_length=self.max_snippet_length,
        )
        self.status = results
        return results

    def fetch_content_text(self) -> Message:
        """执行搜索并以纯文本合并输出。

        契约：输入无；输出 `Message`；副作用：调用外部 API 并更新 `self.status`；
        失败语义：搜索异常透传为 `ToolException`。
        关键路径：1) 调用 `fetch_content` 2) 拼接 `text` 字段 3) 返回消息。
        决策：仅拼接 `text` 字段
        问题：减少输出噪声，便于 LLM 消费
        方案：逐条追加 `item.text`
        代价：丢失标题与链接
        重评：当需要引用链接时增加格式化模板
        """
        data = self.fetch_content()
        result_string = ""
        for item in data:
            result_string += item.text + "\n"
        self.status = result_string
        return Message(text=result_string)
