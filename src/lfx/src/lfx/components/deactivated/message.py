"""
模块名称：Message 构建组件（已停用）

本模块提供构建 `Message` 对象的能力，主要用于旧流程中手动组装聊天消息。主要功能包括：
- 设置发送者/文本/会话 ID 等字段并生成 `Message`

关键组件：
- `MessageComponent`：消息构建组件

设计背景：早期流程需要手动构造消息对象传递给下游。
注意事项：会话 ID 为空时不会绑定历史上下文。
"""

from lfx.custom.custom_component.custom_component import CustomComponent
from lfx.schema.message import Message
from lfx.utils.constants import MESSAGE_SENDER_AI, MESSAGE_SENDER_USER


class MessageComponent(CustomComponent):
    """Message 构建组件。

    契约：返回 `Message` 实例，必要字段由输入提供。
    失败语义：无显式校验，字段错误由 `Message` 类型处理。
    副作用：更新组件 `status`。
    """
    display_name = "Message"
    description = "Creates a Message object given a Session ID."
    name = "Message"

    def build_config(self):
        """返回输入配置。

        契约：`sender` 选项仅允许 AI/User 两类。
        失败语义：无。
        副作用：无。
        """
        return {
            "sender": {
                "options": [MESSAGE_SENDER_AI, MESSAGE_SENDER_USER],
                "display_name": "Sender Type",
            },
            "sender_name": {"display_name": "Sender Name"},
            "text": {"display_name": "Text"},
            "session_id": {
                "display_name": "Session ID",
                "info": "Session ID of the chat history.",
                "input_types": ["Message"],
            },
        }

    def build(
        self,
        sender: str = MESSAGE_SENDER_USER,
        sender_name: str | None = None,
        session_id: str | None = None,
        text: str = "",
    ) -> Message:
        """构建并返回 `Message`。

        契约：若存在 `graph.flow_id` 则写入消息用于追踪。
        失败语义：无显式错误处理。
        副作用：更新组件 `status`。
        """
        flow_id = self.graph.flow_id if hasattr(self, "graph") else None
        message = Message(text=text, sender=sender, sender_name=sender_name, flow_id=flow_id, session_id=session_id)

        self.status = message
        return message
