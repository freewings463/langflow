"""
模块名称：消息存储组件（已停用）

本模块提供将消息写入持久化存储的能力，主要用于旧流程中记录聊天历史。主要功能包括：
- 调用 `astore_message` 保存消息
- 返回最新消息并更新状态

关键组件：
- `StoreMessageComponent`：消息存储组件

设计背景：早期流程需要显式存储消息以供后续检索。
注意事项：依赖 `lfx.memory` 的存储实现与权限配置。
"""

from lfx.custom.custom_component.custom_component import CustomComponent
from lfx.memory import aget_messages, astore_message
from lfx.schema.message import Message


class StoreMessageComponent(CustomComponent):
    """消息存储组件。

    契约：输入 `Message`，存储后返回原消息。
    失败语义：存储失败时抛异常。
    副作用：写入消息存储并更新组件 `status`。
    """
    display_name = "Store Message"
    description = "Stores a chat message."
    name = "StoreMessage"

    def build_config(self):
        """返回输入配置。

        契约：仅需 `message` 输入。
        失败语义：无。
        副作用：无。
        """
        return {
            "message": {"display_name": "Message"},
        }

    async def build(
        self,
        message: Message,
    ) -> Message:
        """异步存储消息并返回。

        契约：如果存在 `graph.flow_id` 则写入存储用于追踪。
        失败语义：存储或读取失败时抛异常。
        副作用：写入存储并刷新 `status`。
        """
        flow_id = self.graph.flow_id if hasattr(self, "graph") else None
        await astore_message(message, flow_id=flow_id)
        self.status = await aget_messages()

        return message
