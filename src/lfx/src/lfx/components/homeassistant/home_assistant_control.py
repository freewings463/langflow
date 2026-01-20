"""
模块名称：home_assistant_control

本模块提供 Home Assistant 控制组件，用于通过 REST API 执行设备动作。
主要功能包括：
- 功能1：根据 action/entity_id 调用 Home Assistant 服务。
- 功能2：提供工具化入口供 Agent 调用。

使用场景：在流程或 Agent 中控制开关、灯、窗帘等设备。
关键组件：
- 类 `HomeAssistantControl`

设计背景：将 Home Assistant 控制逻辑封装为工具组件，减少手写 API 调用。
注意事项：entity_id 必须存在且合法；建议先调用状态列表组件获取。
"""

import json
from typing import Any

import requests
from langchain.tools import StructuredTool
from pydantic import BaseModel, Field

from lfx.base.langchain_utilities.model import LCToolComponent
from lfx.field_typing import Tool
from lfx.inputs.inputs import SecretStrInput, StrInput
from lfx.schema.data import Data


class HomeAssistantControl(LCToolComponent):
    """Home Assistant 设备控制组件。

    契约：输入包含 `ha_token/base_url` 以及动作与实体 ID；输出 `Data` 或工具结果。
    关键路径：
    1) 解析 action/entity_id；
    2) 从 entity_id 提取 domain；
    3) 调用 Home Assistant REST API。
    异常流：网络异常返回错误字符串；由 `_make_data_response` 转为 `Data`。
    排障入口：返回的错误文本包含异常详情。
    决策：
    问题：Agent 需要最少参数即可控制设备。
    方案：仅暴露 `action` 与 `entity_id`，域名从 entity_id 推导。
    代价：entity_id 不可推断，必须由外部提供。
    重评：当需要支持复杂服务参数时。
    """

    display_name: str = "Home Assistant Control"
    description: str = (
        "A very simple tool to control Home Assistant devices. "
        "Only action (turn_on, turn_off, toggle) and entity_id need to be provided."
    )
    documentation: str = "https://developers.home-assistant.io/docs/api/rest/"
    icon: str = "HomeAssistant"

    # 注意：UI 输入字段包含鉴权与默认动作/实体配置。
    inputs = [
        SecretStrInput(
            name="ha_token",
            display_name="Home Assistant Token",
            info="Home Assistant Long-Lived Access Token",
            required=True,
        ),
        StrInput(
            name="base_url",
            display_name="Home Assistant URL",
            info="e.g., http://192.168.0.10:8123",
            required=True,
        ),
        StrInput(
            name="default_action",
            display_name="Default Action (Optional)",
            info="One of turn_on, turn_off, toggle",
            required=False,
        ),
        StrInput(
            name="default_entity_id",
            display_name="Default Entity ID (Optional)",
            info="Default entity ID to control (e.g., switch.unknown_switch_3)",
            required=False,
        ),
    ]

    # 注意：仅向 Agent 暴露 action 与 entity_id。
    class ToolSchema(BaseModel):
        """Agent 传入参数：action 与 entity_id。"""

        action: str = Field(..., description="Home Assistant service name. (One of turn_on, turn_off, toggle)")
        entity_id: str = Field(
            ...,
            description="Entity ID to control (e.g., switch.xxx, light.xxx, cover.xxx, etc.)."
            "Do not infer; use the list_homeassistant_states tool to retrieve it.",
        )

    def run_model(self) -> Data:
        """在 LangFlow 中点击 Run 时执行。

        契约：使用 UI 中配置的默认 action/entity_id。
        关键路径：读取默认值 -> 调用 `_control_device` -> 返回 `Data`。
        决策：
        问题：无 Agent 调用时仍需手动触发控制。
        方案：使用 UI 默认值作为输入。
        代价：默认值错误会导致请求失败。
        重评：当需要交互式输入时。
        """
        action = self.default_action or "turn_off"
        entity_id = self.default_entity_id or "switch.unknown_switch_3"

        result = self._control_device(
            ha_token=self.ha_token,
            base_url=self.base_url,
            action=action,
            entity_id=entity_id,
        )
        return self._make_data_response(result)

    def build_tool(self) -> Tool:
        """构建供 Agent 调用的工具。

        契约：Agent 仅可传入 `action/entity_id`。
        关键路径：创建 `StructuredTool` 并绑定参数 schema。
        决策：
        问题：避免泄露 token/base_url 给 Agent。
        方案：仅暴露最小参数，其他字段保留在组件实例。
        代价：Agent 无法动态修改连接信息。
        重评：当需要多 Home Assistant 实例支持时。
        """
        return StructuredTool.from_function(
            name="home_assistant_control",
            description=(
                "A tool to control Home Assistant devices easily. "
                "Parameters: action ('turn_on'/'turn_off'/'toggle'), entity_id ('switch.xxx', etc.)."
                "Entity ID must be obtained using the list_homeassistant_states tool and not guessed."
            ),
            func=self._control_device_for_tool,  # 注意：工具包装函数。
            args_schema=self.ToolSchema,
        )

    def _control_device_for_tool(self, action: str, entity_id: str) -> dict[str, Any] | str:
        """Agent 调用入口，内部转发到 `_control_device`。

        契约：返回 dict 或错误字符串。
        关键路径：直接转发到 `_control_device`。
        决策：
        问题：保持工具入口与核心逻辑解耦。
        方案：提供轻量包装函数。
        代价：无。
        重评：当需要更多参数转换时。
        """
        return self._control_device(
            ha_token=self.ha_token,
            base_url=self.base_url,
            action=action,
            entity_id=entity_id,
        )

    def _control_device(
        self,
        ha_token: str,
        base_url: str,
        action: str,
        entity_id: str,
    ) -> dict[str, Any] | str:
        """调用 Home Assistant 服务的核心逻辑。

        契约：`entity_id` 形如 `domain.name`；返回 JSON 或错误字符串。
        关键路径：解析 domain -> 组装 URL -> POST 调用服务。
        异常流：请求失败返回错误字符串。
        决策：
        问题：服务域由 entity_id 决定，避免额外输入。
        方案：用 `entity_id.split(".")[0]` 提取 domain。
        代价：entity_id 格式错误会导致异常或错误响应。
        重评：当需要支持更复杂服务（含额外参数）时。
        """
        try:
            domain = entity_id.split(".")[0]  # 注意：从 entity_id 提取 domain（switch/light/cover 等）。
            url = f"{base_url}/api/services/{domain}/{action}"

            headers = {
                "Authorization": f"Bearer {ha_token}",
                "Content-Type": "application/json",
            }
            payload = {"entity_id": entity_id}

            response = requests.post(url, headers=headers, json=payload, timeout=10)
            response.raise_for_status()

            return response.json()  # 注意：成功时返回 HA 响应 JSON。
        except requests.exceptions.RequestException as e:
            return f"Error: Failed to call service. {e}"
        except Exception as e:  # noqa: BLE001
            return f"An unexpected error occurred: {e}"

    def _make_data_response(self, result: dict[str, Any] | str) -> Data:
        """将结果格式化为 LangFlow 的 `Data`。

        契约：dict 返回结构化 data 与 JSON 文本；错误字符串返回 text。
        关键路径：判别类型 -> 构建 `Data`。
        决策：
        问题：需要统一返回格式供下游处理。
        方案：dict 转 JSON 字符串，错误直接作为文本。
        代价：JSON 文本可能较长。
        重评：当需要结构化错误码输出时。
        """
        if isinstance(result, str):
            # 注意：错误信息直接作为文本返回。
            return Data(text=result)

        # 实现：将 dict 转为格式化 JSON 便于展示。
        formatted_json = json.dumps(result, indent=2, ensure_ascii=False)
        return Data(data=result, text=formatted_json)
