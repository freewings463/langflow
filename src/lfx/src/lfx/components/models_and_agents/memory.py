"""
模块名称：消息记忆组件

本模块提供消息存取能力，支持 Langflow 内置存储与外部 Memory 接入。
主要功能：
- 存储消息到内部表或外部 Memory；
- 按条件检索消息并输出文本/数据表；
- 基于模式切换动态显示输入/输出。

关键组件：
- MemoryComponent：消息存取组件入口。

设计背景：统一不同记忆后端的读取/写入方式，便于组件化复用。
注意事项：外部 Memory 需实现 `aget_messages/aadd_messages` 接口。
"""

from typing import Any, cast

from lfx.custom.custom_component.component import Component
from lfx.helpers.data import data_to_text
from lfx.inputs.inputs import DropdownInput, HandleInput, IntInput, MessageTextInput, MultilineInput, TabInput
from lfx.memory import aget_messages, astore_message
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame
from lfx.schema.dotdict import dotdict
from lfx.schema.message import Message
from lfx.template.field.base import Output
from lfx.utils.component_utils import set_current_fields, set_field_display
from lfx.utils.constants import MESSAGE_SENDER_AI, MESSAGE_SENDER_NAME_AI, MESSAGE_SENDER_USER


class MemoryComponent(Component):
    """消息存取组件封装

    契约：支持 `Store/Retrieve` 两种模式；输出为 `Message` 或 `DataFrame`。
    关键路径：1) 模式选择 2) 调用外部或内部存储 3) 返回格式化结果。
    决策：优先兼容外部 Memory 接口
    问题：用户可能接入自定义记忆实现
    方案：检测 `aget_messages/aadd_messages` 并透传调用
    代价：外部接口不一致会导致运行时错误
    重评：当 Memory 接口标准化并可校验时
    """

    display_name = "Message History"
    description = "Stores or retrieves stored chat messages from Langflow tables or an external memory."
    documentation: str = "https://docs.langflow.org/message-history"
    icon = "message-square-more"
    name = "Memory"
    default_keys = ["mode", "memory", "session_id", "context_id"]
    mode_config = {
        "Store": ["message", "memory", "sender", "sender_name", "session_id", "context_id"],
        "Retrieve": ["n_messages", "order", "template", "memory", "session_id", "context_id"],
    }

    inputs = [
        TabInput(
            name="mode",
            display_name="Mode",
            options=["Retrieve", "Store"],
            value="Retrieve",
            info="Operation mode: Store messages or Retrieve messages.",
            real_time_refresh=True,
        ),
        MessageTextInput(
            name="message",
            display_name="Message",
            info="The chat message to be stored.",
            tool_mode=True,
            dynamic=True,
            show=False,
        ),
        HandleInput(
            name="memory",
            display_name="External Memory",
            input_types=["Memory"],
            info="Retrieve messages from an external memory. If empty, it will use the Langflow tables.",
            advanced=True,
        ),
        DropdownInput(
            name="sender_type",
            display_name="Sender Type",
            options=[MESSAGE_SENDER_AI, MESSAGE_SENDER_USER, "Machine and User"],
            value="Machine and User",
            info="Filter by sender type.",
            advanced=True,
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
            info="Filter by sender name.",
            advanced=True,
            show=False,
        ),
        IntInput(
            name="n_messages",
            display_name="Number of Messages",
            value=100,
            info="Number of messages to retrieve.",
            advanced=True,
            show=True,
        ),
        MessageTextInput(
            name="session_id",
            display_name="Session ID",
            info="The session ID of the chat. If empty, the current session ID parameter will be used.",
            value="",
            advanced=True,
        ),
        MessageTextInput(
            name="context_id",
            display_name="Context ID",
            info="The context ID of the chat. Adds an extra layer to the local memory.",
            value="",
            advanced=True,
        ),
        DropdownInput(
            name="order",
            display_name="Order",
            options=["Ascending", "Descending"],
            value="Ascending",
            info="Order of the messages.",
            advanced=True,
            tool_mode=True,
            required=True,
        ),
        MultilineInput(
            name="template",
            display_name="Template",
            info="The template to use for formatting the data. "
            "It can contain the keys {text}, {sender} or any other key in the message data.",
            value="{sender_name}: {text}",
            advanced=True,
            show=False,
        ),
    ]

    outputs = [
        Output(display_name="Message", name="messages_text", method="retrieve_messages_as_text", dynamic=True),
        Output(display_name="Dataframe", name="dataframe", method="retrieve_messages_dataframe", dynamic=True),
    ]

    def update_outputs(self, frontend_node: dict, field_name: str, field_value: Any) -> dict:
        """根据模式切换输出端口

        契约：修改 `frontend_node["outputs"]` 并返回更新后的节点配置。
        关键路径：1) 清空旧输出 2) 按 `Store/Retrieve` 生成输出列表。
        异常流：字段不匹配时保持现状，不抛异常。
        决策：输出随模式动态变化以避免误连
        问题：存储与检索的输出语义不同
        方案：Store 仅返回存储结果，Retrieve 返回文本与表格
        代价：切换模式会丢失原有输出连接
        重评：当 UI 支持条件输出绑定时
        """
        if field_name == "mode":
            # 注意：先清空输出，避免遗留旧模式端口。
            frontend_node["outputs"] = []
            if field_value == "Store":
                frontend_node["outputs"] = [
                    Output(
                        display_name="Stored Messages",
                        name="stored_messages",
                        method="store_message",
                        hidden=True,
                        dynamic=True,
                    )
                ]
            if field_value == "Retrieve":
                frontend_node["outputs"] = [
                    Output(
                        display_name="Messages", name="messages_text", method="retrieve_messages_as_text", dynamic=True
                    ),
                    Output(
                        display_name="Dataframe", name="dataframe", method="retrieve_messages_dataframe", dynamic=True
                    ),
                ]
        return frontend_node

    async def store_message(self) -> Message:
        """存储消息并返回最新写入结果

        契约：输入 `message/session_id/context_id` 等字段；成功返回 `Message`。
        关键路径：1) 规范化消息对象 2) 写入外部或内部存储 3) 取回并返回。
        异常流：无可返回消息时抛 `ValueError`。
        排障入口：异常信息包含 session/sender 关键字段。
        决策：优先使用外部 Memory（若提供）
        问题：需要兼容外部持久化存储
        方案：检测 `self.memory` 并走其接口
        代价：外部实现差异可能导致错误
        重评：当仅允许内置存储时
        """
        message = Message(text=self.message) if isinstance(self.message, str) else self.message

        message.context_id = self.context_id or message.context_id
        message.session_id = self.session_id or message.session_id
        message.sender = self.sender or message.sender or MESSAGE_SENDER_AI
        message.sender_name = self.sender_name or message.sender_name or MESSAGE_SENDER_NAME_AI

        stored_messages: list[Message] = []

        if self.memory:
            self.memory.context_id = message.context_id
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
                    session_id=message.session_id,
                    context_id=message.context_id,
                    sender_name=message.sender_name,
                    sender=message.sender,
                )
                or []
            )

        if not stored_messages:
            msg = "No messages were stored. Please ensure that the session ID and sender are properly set."
            raise ValueError(msg)

        stored_message = stored_messages[0]
        self.status = stored_message
        return stored_message

    async def retrieve_messages(self) -> Data:
        """按条件检索消息列表

        契约：支持按 `sender_type/sender_name/session_id/context_id` 过滤；返回 `Data`（消息列表）。
        关键路径：1) 解析过滤条件 2) 调用外部/内部存储 3) 按数量/排序裁剪。
        异常流：外部 Memory 缺少接口时抛 `AttributeError`。
        性能瓶颈：内部检索可达 `limit=10000`，高频调用需注意。
        排障入口：异常提示包含缺失方法名。
        决策：外部 Memory 由组件负责裁剪/排序
        问题：不同 Memory 的返回顺序不一致
        方案：按 `n_messages/order` 在组件内处理
        代价：额外内存与 CPU 开销
        重评：当外部 Memory 支持原生分页/排序时
        """
        sender_type = self.sender_type
        sender_name = self.sender_name
        session_id = self.session_id
        context_id = self.context_id
        n_messages = self.n_messages
        order = "DESC" if self.order == "Descending" else "ASC"

        if sender_type == "Machine and User":
            sender_type = None

        if self.memory and not hasattr(self.memory, "aget_messages"):
            memory_name = type(self.memory).__name__
            err_msg = f"External Memory object ({memory_name}) must have 'aget_messages' method."
            raise AttributeError(err_msg)
        # 注意：n_messages 为 0 时直接返回空列表。
        if n_messages == 0:
            stored = []
        elif self.memory:
            # 实现：外部 memory 使用当前 session/context 覆盖配置。
            self.memory.session_id = session_id
            self.memory.context_id = context_id

            stored = await self.memory.aget_messages()
            # 注意：LangChain memory 默认按升序返回。

            if n_messages:
                stored = stored[-n_messages:]  # 注意：先取最后 N 条。

            if order == "DESC":
                stored = stored[::-1]  # 注意：需要降序时再反转。

            stored = [Message.from_lc_message(m) for m in stored]
            if sender_type:
                expected_type = MESSAGE_SENDER_AI if sender_type == MESSAGE_SENDER_AI else MESSAGE_SENDER_USER
                stored = [m for m in stored if m.type == expected_type]
        else:
            # 注意：内部存储以 order 控制排序，再裁剪最后 N 条。
            stored = await aget_messages(
                sender=sender_type,
                sender_name=sender_name,
                session_id=session_id,
                context_id=context_id,
                limit=10000,
                order=order,
            )
            if n_messages:
                stored = stored[-n_messages:]  # 注意：取最后 N 条。

        # 注意：状态输出保留给调试场景，默认关闭。
        return cast("Data", stored)

    async def retrieve_messages_as_text(self) -> Message:
        """将检索结果渲染为模板文本

        契约：返回 `Message` 文本；依赖 `template` 字段。
        关键路径：1) 读取消息 2) 按模板渲染。
        决策：使用模板渲染而非固定拼接
        问题：不同场景需要不同展示格式
        方案：模板驱动格式化
        代价：模板错误会导致输出异常
        重评：当输出格式固定化时
        """
        stored_text = data_to_text(self.template, await self.retrieve_messages())
        # 注意：状态输出保留给调试场景，默认关闭。
        return Message(text=stored_text)

    async def retrieve_messages_dataframe(self) -> DataFrame:
        """将检索结果转换为 DataFrame

        契约：返回 `DataFrame`；字段来自 `Message` 对象。
        关键路径：1) 读取消息 2) 直接构造 DataFrame。
        决策：使用 DataFrame 作为表格输出格式
        问题：前端需要表格化展示
        方案：统一输出 DataFrame
        代价：字段变更需同步前端展示
        重评：当前端支持自定义表格时
        """
        messages = await self.retrieve_messages()
        return DataFrame(messages)

    def update_build_config(
        self,
        build_config: dotdict,
        field_value: Any,  # noqa: ARG002
        field_name: str | None = None,  # noqa: ARG002
    ) -> dotdict:
        """按模式切换可见字段

        契约：返回更新后的 `build_config`；副作用：字段显示与默认值被调整。
        关键路径：1) 读取当前模式 2) 应用字段显示规则 3) 返回配置。
        决策：通过 `set_current_fields` 统一字段切换
        问题：手工切换字段容易遗漏
        方案：使用公共工具函数集中处理
        代价：依赖工具函数的规则完整性
        重评：当字段切换规则变复杂时
        """
        return set_current_fields(
            build_config=build_config,
            action_fields=self.mode_config,
            selected_action=build_config["mode"]["value"],
            default_fields=self.default_keys,
            func=set_field_display,
        )
