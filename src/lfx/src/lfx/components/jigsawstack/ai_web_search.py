"""
模块名称：JigsawStack AI Web Search 组件

本模块封装 JigsawStack `web.search`，用于 Web 搜索与 AI 概述生成。
主要功能包括：
- 请求参数标准化（拼写检查/安全搜索/AI 概述）
- 统一返回结构，便于 Langflow 下游消费
- 失败语义集中处理并反馈到 `self.status`

关键组件：
- JigsawStackAIWebSearchComponent：AI Search 组件入口

设计背景：将 JigsawStack 搜索能力适配为 Langflow 组件。
注意事项：依赖 `jigsawstack>=0.2.7` 且需要有效 `api_key`。
"""

from lfx.custom.custom_component.component import Component
from lfx.io import BoolInput, DropdownInput, Output, QueryInput, SecretStrInput
from lfx.schema.data import Data
from lfx.schema.message import Message


class JigsawStackAIWebSearchComponent(Component):
    """JigsawStack AI Web Search 组件封装。

    契约：输入由 `inputs` 定义，输出 `Data` 或 `Message`。
    副作用：网络调用 JigsawStack 搜索服务并更新 `self.status`。
    失败语义：SDK 缺失抛 `ImportError`；API 失败返回失败 `Data`/`Message`。
    """

    display_name = "AI Web Search"
    description = "Effortlessly search the Web and get access to high-quality results powered with AI."
    documentation = "https://jigsawstack.com/docs/api-reference/web/ai-search"
    icon = "JigsawStack"
    name = "JigsawStackAISearch"
    inputs = [
        SecretStrInput(
            name="api_key",
            display_name="JigsawStack API Key",
            info="Your JigsawStack API key for authentication",
            required=True,
        ),
        QueryInput(
            name="query",
            display_name="Query",
            info="The search value. The maximum query character length is 400",
            required=True,
            tool_mode=True,
        ),
        BoolInput(
            name="ai_overview",
            display_name="AI Overview",
            info="Include AI powered overview in the search results",
            required=False,
            value=True,
        ),
        DropdownInput(
            name="safe_search",
            display_name="Safe Search",
            info="Enable safe search to filter out adult content",
            required=False,
            options=["moderate", "strict", "off"],
            value="off",
        ),
        BoolInput(
            name="spell_check",
            display_name="Spell Check",
            info="Spell check the search query",
            required=False,
            value=True,
        ),
    ]

    outputs = [
        Output(display_name="AI Search Results", name="search_results", method="search"),
        Output(display_name="Content Text", name="content_text", method="get_content_text"),
    ]

    def search(self) -> Data:
        """执行 AI 搜索并返回结构化结果。

        契约：输入为 `query` 与可选参数，输出为 `Data`。
        副作用：触发网络请求并更新 `self.status`。
        失败语义：API `success=False` 抛 `ValueError`；SDK 异常返回失败 `Data`。

        关键路径（三步）：
        1) 组装 `query` 与可选参数；
        2) 调用 `client.web.search`；
        3) 归一化响应并写入 `self.status`。

        排障入口：`self.status` 文本。
        """
        try:
            from jigsawstack import JigsawStack, JigsawStackError
        except ImportError as e:
            jigsawstack_import_error = (
                "JigsawStack package not found. Please install it using: pip install jigsawstack>=0.2.7"
            )
            raise ImportError(jigsawstack_import_error) from e

        try:
            client = JigsawStack(api_key=self.api_key)

            # 实现：仅发送非空参数，保持与 SDK 的可选字段语义一致
            search_params = {}
            if self.query:
                search_params["query"] = self.query
            if self.ai_overview is not None:
                search_params["ai_overview"] = self.ai_overview
            if self.safe_search:
                search_params["safe_search"] = self.safe_search
            if self.spell_check is not None:
                search_params["spell_check"] = self.spell_check

            # 实现：调用 JigsawStack 搜索服务
            response = client.web.search(search_params)

            api_error_msg = "JigsawStack API returned unsuccessful response"
            if not response.get("success", False):
                raise ValueError(api_error_msg)

            # 实现：归一化输出字段，便于 Langflow 下游使用
            result_data = {
                "query": self.query,
                "ai_overview": response.get("ai_overview", ""),
                "spell_fixed": response.get("spell_fixed", False),
                "is_safe": response.get("is_safe", True),
                "results": response.get("results", []),
                "success": True,
            }

            self.status = f"Search complete for: {response.get('query', '')}"

            return Data(data=result_data)

        except JigsawStackError as e:
            error_data = {"error": str(e), "success": False}
            self.status = f"Error: {e!s}"
            return Data(data=error_data)

    def get_content_text(self) -> Message:
        """仅返回 AI 概述文本内容。

        契约：输入使用 `query` 与可选参数；输出 `Message(text=ai_overview)`。
        失败语义：SDK 缺失返回错误文本；API 失败抛 `JigsawStackError` 后返回错误文本。
        副作用：触发网络调用。
        """
        try:
            from jigsawstack import JigsawStack, JigsawStackError
        except ImportError:
            return Message(text="Error: JigsawStack package not found.")

        try:
            # 实现：构建客户端并复用与 `search` 相同的参数语义
            client = JigsawStack(api_key=self.api_key)
            search_params = {}
            if self.query:
                search_params["query"] = self.query
            if self.ai_overview is not None:
                search_params["ai_overview"] = self.ai_overview
            if self.safe_search:
                search_params["safe_search"] = self.safe_search
            if self.spell_check is not None:
                search_params["spell_check"] = self.spell_check

            # 实现：调用搜索并读取 `ai_overview`
            response = client.web.search(search_params)

            request_failed_msg = "Request Failed"
            if not response.get("success", False):
                raise JigsawStackError(request_failed_msg)

            content = response.get("ai_overview", "")
            return Message(text=content)

        except JigsawStackError as e:
            return Message(text=f"Error while using AI Search: {e!s}")
