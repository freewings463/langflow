"""
模块名称：agentql_api

本模块提供 AgentQL 组件实现，通过外部 API 从网页抽取结构化数据。
主要功能包括：
- 组装请求参数并调用 AgentQL REST API
- 解析返回数据并输出为 Langflow `Data`

关键组件：
- `AgentQL`：面向用户的 Web 数据抽取组件

设计背景：需要将 AgentQL 能力接入 Langflow 组件体系
使用场景：在流程中以 URL + Query/Prompt 抽取页面数据
注意事项：查询与提示语只能二选一，否则直接报错
"""

import httpx

from lfx.custom.custom_component.component import Component
from lfx.field_typing.range_spec import RangeSpec
from lfx.io import BoolInput, DropdownInput, IntInput, MessageTextInput, MultilineInput, Output, SecretStrInput
from lfx.log.logger import logger
from lfx.schema.data import Data


class AgentQL(Component):
    """AgentQL Web 数据抽取组件。

    契约：需提供 `api_key` 与 `url`，并在 `query`/`prompt` 中二选一。
    副作用：向外部 AgentQL API 发起网络请求。
    失败语义：HTTP 异常或参数冲突会抛 `ValueError` 并写入 `status`。
    排障入口：错误日志关键字 `Failure response` + `status` 提示。
    """

    display_name = "Extract Web Data"
    description = "Extracts structured data from a web page using an AgentQL query or a Natural Language description."
    documentation: str = "https://docs.agentql.com/rest-api/api-reference"
    icon = "AgentQL"
    name = "AgentQL"

    inputs = [
        SecretStrInput(
            name="api_key",
            display_name="AgentQL API Key",
            required=True,
            password=True,
            info="Your AgentQL API key from dev.agentql.com",
        ),
        MessageTextInput(
            name="url",
            display_name="URL",
            required=True,
            info="The URL of the public web page you want to extract data from.",
            tool_mode=True,
        ),
        MultilineInput(
            name="query",
            display_name="AgentQL Query",
            required=False,
            info="The AgentQL query to execute. Learn more at https://docs.agentql.com/agentql-query or use a prompt.",
            tool_mode=True,
        ),
        MultilineInput(
            name="prompt",
            display_name="Prompt",
            required=False,
            info="A Natural Language description of the data to extract from the page. Alternative to AgentQL query.",
            tool_mode=True,
        ),
        BoolInput(
            name="is_stealth_mode_enabled",
            display_name="Enable Stealth Mode (Beta)",
            info="Enable experimental anti-bot evasion strategies. May not work for all websites at all times.",
            value=False,
            advanced=True,
        ),
        IntInput(
            name="timeout",
            display_name="Timeout",
            info="Seconds to wait for a request.",
            value=900,
            advanced=True,
        ),
        DropdownInput(
            name="mode",
            display_name="Request Mode",
            info="'standard' uses deep data analysis, while 'fast' trades some depth of analysis for speed.",
            options=["fast", "standard"],
            value="fast",
            advanced=True,
        ),
        IntInput(
            name="wait_for",
            display_name="Wait For",
            info="Seconds to wait for the page to load before extracting data.",
            value=0,
            range_spec=RangeSpec(min=0, max=10, step_type="int"),
            advanced=True,
        ),
        BoolInput(
            name="is_scroll_to_bottom_enabled",
            display_name="Enable scroll to bottom",
            info="Scroll to bottom of the page before extracting data.",
            value=False,
            advanced=True,
        ),
        BoolInput(
            name="is_screenshot_enabled",
            display_name="Enable screenshot",
            info="Take a screenshot before extracting data. Returned in 'metadata' as a Base64 string.",
            value=False,
            advanced=True,
        ),
    ]

    outputs = [
        Output(display_name="Data", name="data", method="build_output"),
    ]

    def build_output(self) -> Data:
        """执行 AgentQL 请求并返回结构化数据。

        契约：必须提供 `url`，且 `query`/`prompt` 只能选其一；返回 `Data`。
        副作用：发送 HTTP POST；失败时写 `self.status`。
        失败语义：参数冲突抛 `ValueError`；HTTP 失败转为 `ValueError`。
        关键路径（三步）：1) 构造 headers/payload 2) 发起请求并校验 3) 解析为 `Data`。
        异常流：401 转为“无效 API Key”，其余按响应体拼接错误信息。
        性能瓶颈：网络 RTT 与目标页面解析耗时（受 `timeout` 影响）。
        排障入口：日志关键字 `Failure response`，响应体含 `error_info/detail`。
        决策：要求 `query` 与 `prompt` 互斥。
        问题：两者同时存在会导致服务端语义不确定。
        方案：本地校验并提前拒绝。
        代价：调用方需要在前端做额外校验。
        重评：当服务端支持同时合并策略时。
        """
        endpoint = "https://api.agentql.com/v1/query-data"
        headers = {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json",
            "X-TF-Request-Origin": "langflow",
        }

        payload = {
            "url": self.url,
            "query": self.query,
            "prompt": self.prompt,
            "params": {
                "mode": self.mode,
                "wait_for": self.wait_for,
                "is_scroll_to_bottom_enabled": self.is_scroll_to_bottom_enabled,
                "is_screenshot_enabled": self.is_screenshot_enabled,
            },
            "metadata": {
                "experimental_stealth_mode_enabled": self.is_stealth_mode_enabled,
            },
        }

        if not self.prompt and not self.query:
            self.status = "Either Query or Prompt must be provided."
            raise ValueError(self.status)
        if self.prompt and self.query:
            self.status = "Both Query and Prompt can't be provided at the same time."
            raise ValueError(self.status)

        try:
            response = httpx.post(endpoint, headers=headers, json=payload, timeout=self.timeout)
            response.raise_for_status()

            json = response.json()
            data = Data(result=json["data"], metadata=json["metadata"])

        except httpx.HTTPStatusError as e:
            response = e.response
            if response.status_code == httpx.codes.UNAUTHORIZED:
                self.status = "Please, provide a valid API Key. You can create one at https://dev.agentql.com."
            else:
                try:
                    error_json = response.json()
                    logger.error(
                        f"Failure response: '{response.status_code} {response.reason_phrase}' with body: {error_json}"
                    )
                    msg = error_json["error_info"] if "error_info" in error_json else error_json["detail"]
                except (ValueError, TypeError):
                    msg = f"HTTP {e}."
                self.status = msg
            raise ValueError(self.status) from e

        else:
            self.status = data
            return data
