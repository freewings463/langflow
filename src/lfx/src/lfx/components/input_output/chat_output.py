"""
模块名称：Chat 输出组件

本模块提供在 Playground 展示聊天消息的组件，主要用于将输入数据转换为
标准 `Message` 并写入历史（可选）。
主要功能包括：
- 将 Data/DataFrame/Message/字符串转换为可展示文本
- 组装 `Message` 并设置来源信息
- 按需持久化消息历史

关键组件：
- `ChatOutput`：聊天输出组件

设计背景：统一输出消息格式与来源信息展示。
注意事项：输入类型不合法会抛 `TypeError` 或 `ValueError`。
"""

from collections.abc import Generator
from typing import Any

import orjson
from fastapi.encoders import jsonable_encoder

from lfx.base.io.chat import ChatComponent
from lfx.helpers.data import safe_convert
from lfx.inputs.inputs import BoolInput, DropdownInput, HandleInput, MessageTextInput
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame
from lfx.schema.message import Message
from lfx.schema.properties import Source
from lfx.template.field.base import Output
from lfx.utils.constants import (
    MESSAGE_SENDER_AI,
    MESSAGE_SENDER_NAME_AI,
    MESSAGE_SENDER_USER,
)


class ChatOutput(ChatComponent):
    """Chat 输出组件。

    契约：`message_response()` 返回 `Message`；可选写入消息历史。
    副作用：可能写入历史并更新 `self.status`。
    失败语义：输入非法时抛 `ValueError`/`TypeError`。
    """
    display_name = "Chat Output"
    description = "Display a chat message in the Playground."
    documentation: str = "https://docs.langflow.org/chat-input-and-output"
    icon = "MessagesSquare"
    name = "ChatOutput"
    minimized = True

    inputs = [
        HandleInput(
            name="input_value",
            display_name="Inputs",
            info="Message to be passed as output.",
            input_types=["Data", "DataFrame", "Message"],
            required=True,
        ),
        BoolInput(
            name="should_store_message",
            display_name="Store Messages",
            info="Store the message in the history.",
            value=True,
            advanced=True,
        ),
        DropdownInput(
            name="sender",
            display_name="Sender Type",
            options=[MESSAGE_SENDER_AI, MESSAGE_SENDER_USER],
            value=MESSAGE_SENDER_AI,
            advanced=True,
            info="Type of sender.",
        ),
        MessageTextInput(
            name="sender_name",
            display_name="Sender Name",
            info="Name of the sender.",
            value=MESSAGE_SENDER_NAME_AI,
            advanced=True,
        ),
        MessageTextInput(
            name="session_id",
            display_name="Session ID",
            info="The session ID of the chat. If empty, the current session ID parameter will be used.",
            advanced=True,
        ),
        MessageTextInput(
            name="context_id",
            display_name="Context ID",
            info="The context ID of the chat. Adds an extra layer to the local memory.",
            value="",
            advanced=True,
        ),
        MessageTextInput(
            name="data_template",
            display_name="Data Template",
            value="{text}",
            advanced=True,
            info="Template to convert Data to Text. If left empty, it will be dynamically set to the Data's text key.",
        ),
        BoolInput(
            name="clean_data",
            display_name="Basic Clean Data",
            value=True,
            advanced=True,
            info="Whether to clean data before converting to string.",
        ),
    ]
    outputs = [
        Output(
            display_name="Output Message",
            name="message",
            method="message_response",
        ),
    ]

    def _build_source(self, id_: str | None, display_name: str | None, source: str | None) -> Source:
        """构建消息来源信息结构。"""
        source_dict = {}
        if id_:
            source_dict["id"] = id_
        if display_name:
            source_dict["display_name"] = display_name
        if source:
            # 注意：处理 ChatOpenAI 等模型对象的来源名称
            if hasattr(source, "model_name"):
                source_dict["source"] = source.model_name
            elif hasattr(source, "model"):
                source_dict["source"] = str(source.model)
            else:
                source_dict["source"] = str(source)
        return Source(**source_dict)

    async def message_response(self) -> Message:
        """构建并输出消息对象。

        关键路径（三步）：
        1) 将输入转换为文本或流式数据
        2) 组装 `Message` 并设置来源信息
        3) 按需写入历史并返回
        异常流：输入非法时由 `_validate_input` 抛出异常。
        """
        # 实现：先转换输入为文本
        text = self.convert_to_string()

        # 实现：获取来源信息
        source, _, display_name, source_id = self.get_properties_from_source_component()

        # 实现：复用或创建 Message
        if isinstance(self.input_value, Message) and not self.is_connected_to_chat_input():
            message = self.input_value
            # 实现：更新消息内容
            message.text = text
            # 注意：保留传入消息的 session_id
            existing_session_id = message.session_id
        else:
            message = Message(text=text)
            existing_session_id = None

        # 实现：设置消息属性
        message.sender = self.sender
        message.sender_name = self.sender_name
        # 注意：优先保留传入 session_id，否则使用组件/图的 session_id
        message.session_id = (
            self.session_id or existing_session_id or (self.graph.session_id if hasattr(self, "graph") else None) or ""
        )
        message.context_id = self.context_id
        message.flow_id = self.graph.flow_id if hasattr(self, "graph") else None
        message.properties.source = self._build_source(source_id, display_name, source)

        # 实现：按需写入历史
        if message.session_id and self.should_store_message:
            stored_message = await self.send_message(message)
            self.message.value = stored_message
            message = stored_message

        self.status = message
        return message

    def _serialize_data(self, data: Data) -> str:
        """将 Data 序列化为 JSON 字符串。"""
        # 实现：转换为可序列化结构
        serializable_data = jsonable_encoder(data.data)
        # 实现：使用 orjson 序列化并缩进
        json_bytes = orjson.dumps(serializable_data, option=orjson.OPT_INDENT_2)
        # 实现：包装为 Markdown code block
        return "```json\n" + json_bytes.decode("utf-8") + "\n```"

    def _validate_input(self) -> None:
        """校验输入类型并在非法时抛异常。"""
        if self.input_value is None:
            msg = "Input data cannot be None"
            raise ValueError(msg)
        if isinstance(self.input_value, list) and not all(
            isinstance(item, Message | Data | DataFrame | str) for item in self.input_value
        ):
            invalid_types = [
                type(item).__name__
                for item in self.input_value
                if not isinstance(item, Message | Data | DataFrame | str)
            ]
            msg = f"Expected Data or DataFrame or Message or str, got {invalid_types}"
            raise TypeError(msg)
        if not isinstance(
            self.input_value,
            Message | Data | DataFrame | str | list | Generator | type(None),
        ):
            type_name = type(self.input_value).__name__
            msg = f"Expected Data or DataFrame or Message or str, Generator or None, got {type_name}"
            raise TypeError(msg)

    def convert_to_string(self) -> str | Generator[Any, None, None]:
        """将输入转换为字符串或生成器。"""
        self._validate_input()
        if isinstance(self.input_value, list):
            clean_data: bool = getattr(self, "clean_data", False)
            return "\n".join([safe_convert(item, clean_data=clean_data) for item in self.input_value])
        if isinstance(self.input_value, Generator):
            return self.input_value
        return safe_convert(self.input_value)
