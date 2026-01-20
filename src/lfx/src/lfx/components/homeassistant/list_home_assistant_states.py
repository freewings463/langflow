"""
模块名称：list_home_assistant_states

本模块提供 Home Assistant 状态列表组件，用于获取设备状态并可按域过滤。
主要功能包括：
- 功能1：调用 `/api/states` 获取所有实体状态。
- 功能2：按 domain 过滤返回结果。

使用场景：在 Agent 执行控制前获取可用实体 ID。
关键组件：
- 类 `ListHomeAssistantStates`

设计背景：为设备控制提供可用实体列表，避免猜测 entity_id。
注意事项：返回结果可能较大；建议使用 filter_domain 限制范围。
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


class ListHomeAssistantStates(LCToolComponent):
    """Home Assistant 状态列表组件。

    契约：输入 `ha_token/base_url`；可选 `filter_domain`；输出 `Data`。
    关键路径：
    1) 调用 `/api/states`；
    2) 按 domain 过滤（若提供）；
    3) 格式化为 `Data`。
    异常流：请求失败返回错误字符串并封装为 `Data`。
    排障入口：错误文本包含异常详情。
    决策：
    问题：Agent 需要准确 entity_id 列表。
    方案：提供状态列表工具，并限制 Agent 参数为 filter_domain。
    代价：返回数据量大时性能与传输成本增加。
    重评：当引入分页或缓存机制时。
    """
    display_name: str = "List Home Assistant States"
    description: str = (
        "Retrieve states from Home Assistant. "
        "The agent only needs to specify 'filter_domain' (optional). "
        "Token and base_url are not exposed to the agent."
    )
    documentation: str = "https://developers.home-assistant.io/docs/api/rest/"
    icon = "HomeAssistant"

    # 注意：UI 输入包含鉴权与默认过滤域。
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
            name="filter_domain",
            display_name="Default Filter Domain (Optional)",
            info="light, switch, sensor, etc. (Leave empty to fetch all)",
            required=False,
        ),
    ]

    # 注意：仅向 Agent 暴露 `filter_domain` 参数。
    class ToolSchema(BaseModel):
        """Agent 传入参数：filter_domain。"""

        filter_domain: str = Field("", description="Filter domain (e.g., 'light'). If empty, returns all.")

    def run_model(self) -> Data:
        """在 LangFlow 中点击 Run 时执行。

        契约：使用 UI 中的 `ha_token/base_url/filter_domain`。
        关键路径：读取默认过滤条件 -> 调用 `_list_states` -> 格式化为 `Data`。
        决策：
        问题：无 Agent 调用时仍需人工测试。
        方案：使用 UI 输入直接执行查询。
        代价：默认过滤可能隐藏部分实体。
        重评：当需要交互式过滤时。
        """
        filter_domain = self.filter_domain or ""  # 注意：空字符串表示返回全部状态。
        result = self._list_states(
            ha_token=self.ha_token,
            base_url=self.base_url,
            filter_domain=filter_domain,
        )
        return self._make_data_response(result)

    def build_tool(self) -> Tool:
        """构建供 Agent 调用的工具。

        契约：Agent 仅能传 `filter_domain`；鉴权信息不暴露。
        关键路径：创建 `StructuredTool` 并绑定 schema。
        决策：
        问题：避免 Agent 看到 token/base_url 等敏感信息。
        方案：将敏感信息存于组件实例，仅暴露过滤参数。
        代价：Agent 无法动态切换实例。
        重评：当需要多实例切换时。
        """
        return StructuredTool.from_function(
            name="list_homeassistant_states",
            description=(
                "Retrieve states from Home Assistant. "
                "You can provide filter_domain='light', 'switch', etc. to narrow results."
            ),
            func=self._list_states_for_tool,  # 注意：工具包装函数。
            args_schema=self.ToolSchema,  # 注意：仅暴露 filter_domain。
        )

    def _list_states_for_tool(self, filter_domain: str = "") -> list[Any] | str:
        """Agent 调用入口，内部转发到 `_list_states`。

        契约：返回列表或错误字符串。
        关键路径：使用实例鉴权信息调用 `_list_states`。
        决策：
        问题：保持工具入口与核心逻辑解耦。
        方案：轻量包装函数。
        代价：无。
        重评：当需要参数转换或预处理时。
        """
        return self._list_states(
            ha_token=self.ha_token,
            base_url=self.base_url,
            filter_domain=filter_domain,
        )

    def _list_states(
        self,
        ha_token: str,
        base_url: str,
        filter_domain: str = "",
    ) -> list[Any] | str:
        """调用 Home Assistant `/api/states` 接口。

        契约：返回状态列表或错误字符串；`filter_domain` 为空返回全部。
        关键路径：请求 states -> 可选过滤 -> 返回结果。
        异常流：请求或解析失败返回错误字符串。
        决策：
        问题：状态列表较大，需支持按域过滤。
        方案：通过 `entity_id` 前缀过滤。
        代价：仅支持 domain 粗粒度过滤。
        重评：当需要更细粒度筛选或分页时。
        """
        try:
            headers = {
                "Authorization": f"Bearer {ha_token}",
                "Content-Type": "application/json",
            }
            url = f"{base_url}/api/states"
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()

            all_states = response.json()
            if filter_domain:
                return [st for st in all_states if st.get("entity_id", "").startswith(f"{filter_domain}.")]

        except requests.exceptions.RequestException as e:
            return f"Error: Failed to fetch states. {e}"
        except (ValueError, TypeError) as e:
            return f"Error processing response: {e}"
        return all_states

    def _make_data_response(self, result: list[Any] | str | dict) -> Data:
        """将结果格式化为 `Data`。

        契约：list/dict 返回 JSON 文本；str 作为错误文本返回。
        关键路径：类型判断 -> 构建 `Data`。
        决策：
        问题：下游需要统一的 `Data` 输出结构。
        方案：统一封装并生成格式化 JSON 文本。
        代价：大型列表会导致文本体积较大。
        重评：当需要分页或摘要输出时。
        """
        try:
            if isinstance(result, list):
                # 实现：将列表包装为字典并转换为 JSON 文本。
                wrapped_result = {"result": result}
                return Data(data=wrapped_result, text=json.dumps(wrapped_result, indent=2, ensure_ascii=False))
            if isinstance(result, dict):
                # 实现：字典类型直接返回。
                return Data(data=result, text=json.dumps(result, indent=2, ensure_ascii=False))
            if isinstance(result, str):
                # 实现：错误字符串直接返回。
                return Data(data={}, text=result)

            # 注意：处理未知数据类型。
            return Data(data={}, text="Error: Unexpected response format.")
        except (TypeError, ValueError) as e:
            # 注意：格式化过程中出现类型或值错误。
            return Data(data={}, text=f"Error: Failed to process response. Details: {e!s}")
