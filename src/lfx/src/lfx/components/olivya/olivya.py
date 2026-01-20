"""
模块名称：Olivya 外呼组件

本模块通过 Olivya 平台发起外呼请求，封装鉴权、参数组装与响应解析。
主要功能包括：
- 构造外呼请求载荷与鉴权头
- 调用 `https://phone.olivya.io/create_zap_call` 发起请求
- 解析响应并以 `Data` 形式输出

关键组件：
- `OlivyaComponent`
- `build_output`

设计背景：在 Langflow 中提供统一的外呼能力入口。
注意事项：请求超时为 10 秒；日志会记录请求载荷与响应摘要。
"""

import json

import httpx

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output
from lfx.log.logger import logger
from lfx.schema.data import Data


class OlivyaComponent(Component):
    """Olivya 外呼组件。

    契约：
    - 输入：API Key、主叫/被叫号码与可选的对话上下文字段
    - 输出：`Data`，包含成功响应或错误信息字典
    - 副作用：发起外部 HTTP 请求并写入日志
    - 失败语义：HTTP 状态错误/网络错误/JSON 解析错误转为错误字典返回
    """

    display_name = "Place Call"
    description = "A component to create an outbound call request from Olivya's platform."
    documentation: str = "https://docs.olivya.io"
    icon = "Olivya"
    name = "OlivyaComponent"

    inputs = [
        MessageTextInput(
            name="api_key",
            display_name="Olivya API Key",
            info="Your API key for authentication",
            value="",
            required=True,
        ),
        MessageTextInput(
            name="from_number",
            display_name="From Number",
            info="The Agent's phone number",
            value="",
            required=True,
        ),
        MessageTextInput(
            name="to_number",
            display_name="To Number",
            info="The recipient's phone number",
            value="",
            required=True,
        ),
        MessageTextInput(
            name="first_message",
            display_name="First Message",
            info="The Agent's introductory message",
            value="",
            required=False,
            tool_mode=True,
        ),
        MessageTextInput(
            name="system_prompt",
            display_name="System Prompt",
            info="The system prompt to guide the interaction",
            value="",
            required=False,
        ),
        MessageTextInput(
            name="conversation_history",
            display_name="Conversation History",
            info="The summary of the conversation",
            value="",
            required=False,
            tool_mode=True,
        ),
    ]

    outputs = [
        Output(display_name="Output", name="output", method="build_output"),
    ]

    async def build_output(self) -> Data:
        """组装外呼请求并返回平台响应。

        关键路径（三步）：
        1) 规整输入字段并构建 `payload`/`headers`
        2) 发送 POST 请求并校验响应状态
        3) 解析 JSON 并封装为 `Data`

        异常流：HTTP 状态码异常、网络异常、JSON 解析异常均捕获并写入错误字段。
        排障入口：日志关键字 `Sending POST request` / `Request successful` / `HTTP error occurred`。
        """
        try:
            payload = {
                "variables": {
                    "first_message": self.first_message.strip() if self.first_message else None,
                    "system_prompt": self.system_prompt.strip() if self.system_prompt else None,
                    "conversation_history": self.conversation_history.strip() if self.conversation_history else None,
                },
                "from_number": self.from_number.strip(),
                "to_number": self.to_number.strip(),
            }

            headers = {
                "Authorization": self.api_key.strip(),
                "Content-Type": "application/json",
            }

            await logger.ainfo("Sending POST request with payload: %s", payload)

            # 注意：外呼请求固定 10 秒超时，超时会进入 RequestError 分支
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://phone.olivya.io/create_zap_call",
                    headers=headers,
                    json=payload,
                    timeout=10.0,
                )
                response.raise_for_status()

                # 注意：响应必须为 JSON，解析失败会进入 JSONDecodeError 分支
                response_data = response.json()
                await logger.ainfo("Request successful: %s", response_data)

        except httpx.HTTPStatusError as http_err:
            await logger.aexception("HTTP error occurred")
            response_data = {"error": f"HTTP error occurred: {http_err}", "response_text": response.text}
        except httpx.RequestError as req_err:
            await logger.aexception("Request failed")
            response_data = {"error": f"Request failed: {req_err}"}
        except json.JSONDecodeError as json_err:
            await logger.aexception("Response parsing failed")
            response_data = {"error": f"Response parsing failed: {json_err}", "raw_response": response.text}
        except Exception as e:  # noqa: BLE001
            await logger.aexception("An unexpected error occurred")
            response_data = {"error": f"An unexpected error occurred: {e!s}"}

        # 注意：无论成功或失败，均以 Data 结构返回
        return Data(value=response_data)
