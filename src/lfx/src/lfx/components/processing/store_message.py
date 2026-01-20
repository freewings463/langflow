"""消息存储组件。

本模块将聊天消息存储到 Langflow 表或外部 Memory。
设计背景：旧组件保留以兼容历史流程。
注意事项：外部 Memory 为空时写入 Langflow 内建存储。
"""

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import (
    HandleInput,
    MessageTextInput,
)
from lfx.memory import aget_messages, astore_message
from lfx.schema.message import Message
from lfx.template.field.base import Output
from lfx.utils.constants import MESSAGE_SENDER_AI, MESSAGE_SENDER_NAME_AI


class MessageStoreComponent(Component):
    """消息存储组件封装。

    契约：输入为消息文本与可选 Memory；输出为已存储的 Message。
    副作用：写入外部或本地存储并更新 `self.status`。
    失败语义：未找到存储结果时抛 `ValueError`。
    """
    display_name = "Message Store"
    description = "Stores a chat message or text into Langflow tables or an external memory."
    icon = "message-square-text"
    name = "StoreMessage"
    legacy = True
    replacement = ["helpers.Memory"]

    inputs = [
        MessageTextInput(
            name="message", display_name="Message", info="The chat message to be stored.", required=True, tool_mode=True
        ),
        HandleInput(
            name="memory",
            display_name="External Memory",
            input_types=["Memory"],
            info="The external memory to store the message. If empty, it will use the Langflow tables.",
        ),
        MessageTextInput(
            name="sender",
            display_name="Sender",
            info="The sender of the message. Might be Machine or User. "
            "If empty, the current sender parameter will be used.",
            advanced=True,
        ),
        MessageTextInput(
            name="sender_name",
            display_name="Sender Name",
            info="The name of the sender. Might be AI or User. If empty, the current sender parameter will be used.",
            advanced=True,
        ),
        MessageTextInput(
            name="session_id",
            display_name="Session ID",
            info="The session ID of the chat. If empty, the current session ID parameter will be used.",
            value="",
            advanced=True,
        ),
    ]

    outputs = [
        Output(display_name="Stored Messages", name="stored_messages", method="store_message", hidden=True),
    ]

    async def store_message(self) -> Message:
        """存储消息并返回最新记录。

        关键路径（三步）：
        1) 规范化消息与会话字段；
        2) 写入外部 Memory 或本地存储；
        3) 读取并返回最新消息。
        """
        message = Message(text=self.message) if isinstance(self.message, str) else self.message

        message.session_id = self.session_id or message.session_id
        message.sender = self.sender or message.sender or MESSAGE_SENDER_AI
        message.sender_name = self.sender_name or message.sender_name or MESSAGE_SENDER_NAME_AI

        stored_messages: list[Message] = []

        if self.memory:
            self.memory.session_id = message.session_id
            lc_message = message.to_lc_message()
            await self.memory.aadd_messages([lc_message])

            stored_messages = await self.memory.aget_messages() or []

            stored_messages = [Message.from_lc_message(m) for m in stored_messages] if stored_messages else []

            if message.sender:
                stored_messages = [m for m in stored_messages if m.sender == message.sender]
        else:
            await astore_message(message, flow_id=self.graph.flow_id)
            stored_messages = (
                await aget_messages(
                    session_id=message.session_id, sender_name=message.sender_name, sender=message.sender
                )
                or []
            )

        if not stored_messages:
            msg = "No messages were stored. Please ensure that the session ID and sender are properly set."
            raise ValueError(msg)

        stored_message = stored_messages[0]
        self.status = stored_message
        return stored_message
