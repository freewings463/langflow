"""
模块名称：`LangChain` 代理组件基类

本模块提供 `LangChain` 代理组件的通用封装，主要用于在 `LangFlow` 中统一代理构建、输入规范化、
事件流处理与消息落库。
主要功能包括：
- 代理执行器构建与运行：封装 `AgentExecutor`/`Runnable` 的兼容路径
- 输入与历史整理：处理多模态输入并避免空输入
- 事件回调与异常语义：统一回调、日志与消息状态

关键组件：
- `LCAgentComponent`：代理组件基类
- `LCToolsAgentComponent`：工具型代理组件基类

设计背景：跨模型与 `LangChain` 版本需要统一执行与事件协议。
注意事项：输入必须保证非空字符串；多模态图片会转入 `chat_history`。
"""

import re
import uuid
from abc import abstractmethod
from typing import TYPE_CHECKING, cast

from langchain.agents import AgentExecutor, BaseMultiActionAgent, BaseSingleActionAgent
from langchain.agents.agent import RunnableAgent
from langchain.callbacks.base import BaseCallbackHandler
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.runnables import Runnable

from lfx.base.agents.callback import AgentAsyncHandler
from lfx.base.agents.events import ExceptionWithMessageError, process_agent_events
from lfx.base.agents.utils import get_chat_output_sender_name
from lfx.custom.custom_component.component import Component, _get_component_toolkit
from lfx.field_typing import Tool
from lfx.inputs.inputs import InputTypes, MultilineInput
from lfx.io import BoolInput, HandleInput, IntInput, MessageInput
from lfx.log.logger import logger
from lfx.memory import delete_message
from lfx.schema.content_block import ContentBlock
from lfx.schema.data import Data
from lfx.schema.log import OnTokenFunctionType
from lfx.schema.message import Message
from lfx.template.field.base import Output
from lfx.utils.constants import MESSAGE_SENDER_AI

if TYPE_CHECKING:
    from lfx.schema.log import OnTokenFunctionType, SendMessageFunctionType


# 决策：默认工具描述和代理名称
# 问题：需要为代理提供默认的工具描述和名称
# 方案：定义常量以供使用
# 代价：硬编码字符串，需要国际化支持
# 重评：当需要多语言支持时重新评估
DEFAULT_TOOLS_DESCRIPTION = "A helpful assistant with access to the following tools:"
DEFAULT_AGENT_NAME = "Agent ({tools_names})"


class LCAgentComponent(Component):
    """LangChain 代理组件的基类

    关键路径（三步）：
    1) 初始化输入参数和配置
    2) 构建代理并运行
    3) 处理响应和事件

    异常流：输入验证失败、代理执行异常、工具调用错误。
    性能瓶颈：复杂工具调用、长时间运行的代理迭代。
    排障入口：日志关键字 "Handle Parse Errors"、"Max Iterations"。
    """
    trace_type = "agent"
    # 注意：`_base_inputs` 定义了代理的基本输入参数
    _base_inputs: list[InputTypes] = [
        MessageInput(
            name="input_value",
            display_name="Input",
            info="The input provided by the user for the agent to process.",
            tool_mode=True,
        ),
        BoolInput(
            name="handle_parsing_errors",
            display_name="Handle Parse Errors",
            value=True,
            advanced=True,
            info="Should the Agent fix errors when reading user input for better processing?",
        ),
        BoolInput(name="verbose", display_name="Verbose", value=True, advanced=True),
        IntInput(
            name="max_iterations",
            display_name="Max Iterations",
            value=15,
            advanced=True,
            info="The maximum number of attempts the agent can make to complete its task before it stops.",
        ),
        MultilineInput(
            name="agent_description",
            display_name="Agent Description [Deprecated]",
            info=(
                "The description of the agent. This is only used when in Tool Mode. "
                f"Defaults to '{DEFAULT_TOOLS_DESCRIPTION}' and tools are added dynamically. "
                "This feature is deprecated and will be removed in future versions."
            ),
            advanced=True,
            value=DEFAULT_TOOLS_DESCRIPTION,
        ),
    ]

    outputs = [
        Output(display_name="Response", name="response", method="message_response"),
        Output(display_name="Agent", name="agent", method="build_agent", tool_mode=False),
    ]

    # 注意：共享回调用于链路追踪并缓存到 `self.shared_callbacks`
    def _get_shared_callbacks(self) -> list[BaseCallbackHandler]:
        """获取共享回调处理器

        契约：
        - 输入：无
        - 输出：BaseCallbackHandler 列表
        - 副作用：初始化 shared_callbacks 属性
        - 失败语义：无
        """
        if not hasattr(self, "shared_callbacks"):
            self.shared_callbacks = self.get_langchain_callbacks()
        return self.shared_callbacks

    @abstractmethod
    def build_agent(self) -> AgentExecutor:
        """构建代理执行器

        契约：
        - 输入：无
        - 输出：AgentExecutor 实例
        - 副作用：无
        - 失败语义：如果构建失败，抛出相应异常
        """

    async def message_response(self) -> Message:
        """运行代理并返回响应

        契约：
        - 输入：无
        - 输出：Message 对象
        - 副作用：设置 self.status 为响应消息
        - 失败语义：如果代理运行失败，抛出相应异常
        """
        agent = self.build_agent()
        message = await self.run_agent(agent=agent)

        self.status = message
        return message

    def _validate_outputs(self) -> None:
        """验证必需的输出方法是否已定义

        契约：
        - 输入：无
        - 输出：无
        - 副作用：验证失败时抛出 ValueError
        - 失败语义：如果必需的输出方法未定义，则抛出 ValueError
        """
        required_output_methods = ["build_agent"]
        output_names = [output.name for output in self.outputs]
        for method_name in required_output_methods:
            if method_name not in output_names:
                msg = f"Output with name '{method_name}' must be defined."
                raise ValueError(msg)
            if not hasattr(self, method_name):
                msg = f"Method '{method_name}' must be defined."
                raise ValueError(msg)

    def get_agent_kwargs(self, *, flatten: bool = False) -> dict:
        """获取代理的关键字参数

        契约：
        - 输入：flatten 参数决定是否展平参数
        - 输出：包含代理配置的字典
        - 副作用：无
        - 失败语义：无
        """
        base = {
            "handle_parsing_errors": self.handle_parsing_errors,
            "verbose": self.verbose,
            "allow_dangerous_code": True,
        }
        agent_kwargs = {
            "handle_parsing_errors": self.handle_parsing_errors,
            "max_iterations": self.max_iterations,
        }
        if flatten:
            return {
                **base,
                **agent_kwargs,
            }
        return {**base, "agent_executor_kwargs": agent_kwargs}

    def get_chat_history_data(self) -> list[Data] | None:
        """获取聊天历史数据

        契约：
        - 输入：无
        - 输出：Data 对象列表或 None
        - 副作用：无
        - 失败语义：无
        """
        # 注意：子类可重写以返回聊天历史
        return None

    def _data_to_messages_skip_empty(self, data: list[Data]) -> list[BaseMessage]:
        """将数据转换为消息，过滤空文本同时保留非文本内容

        契约：
        - 输入：Data 对象列表
        - 输出：BaseMessage 对象列表
        - 副作用：跳过空文本消息
        - 失败语义：无
        
        注意：添加此函数是为了修复某些提供商在收到空文本作为输入时失败的问题。
        """
        messages = []
        for value in data:
            # 注意：仅当存在 `text` 且为空白时跳过
            text = getattr(value, "text", None)
            if isinstance(text, str) and not text.strip():
                # 注意：仅跳过 `text` 为空白的消息
                continue

            lc_message = value.to_lc_message()
            messages.append(lc_message)

        return messages

    async def run_agent(
        self,
        agent: Runnable | BaseSingleActionAgent | BaseMultiActionAgent | AgentExecutor,
    ) -> Message:
        """运行代理并返回消息

        关键路径（三步）：
        1) 准备代理执行环境
        2) 构建输入字典
        3) 执行代理并处理结果

        异常流：输入验证失败、代理执行异常、工具调用错误。
        性能瓶颈：长时间运行的代理迭代、复杂的工具调用。
        排障入口：日志关键字 "ExceptionWithMessageError"、"Anthropic API errors"。
        """
        if isinstance(agent, AgentExecutor):
            runnable = agent
        else:
            # 注意：运行代理不强制依赖工具，因此不做强校验
            handle_parsing_errors = hasattr(self, "handle_parsing_errors") and self.handle_parsing_errors
            verbose = hasattr(self, "verbose") and self.verbose
            max_iterations = hasattr(self, "max_iterations") and self.max_iterations
            runnable = AgentExecutor.from_agent_and_tools(
                agent=agent,
                tools=self.tools or [],
                handle_parsing_errors=handle_parsing_errors,
                verbose=verbose,
                max_iterations=max_iterations,
            )
        # 实现：将 `input_value` 规范化为代理可接受的输入
        lc_message = None
        if isinstance(self.input_value, Message):
            lc_message = self.input_value.to_lc_message()
            # 注意：从 `LangChain` 消息中提取文本作为代理输入
            # 注意：代理期望字符串而非 `Message` 对象
            if hasattr(lc_message, "content"):
                if isinstance(lc_message.content, str):
                    input_dict: dict[str, str | list[BaseMessage] | BaseMessage] = {"input": lc_message.content}
                elif isinstance(lc_message.content, list):
                    # 实现：多模态内容只提取文本部分
                    text_parts = [item.get("text", "") for item in lc_message.content if item.get("type") == "text"]
                    input_dict = {"input": " ".join(text_parts) if text_parts else ""}
                else:
                    input_dict = {"input": str(lc_message.content)}
            else:
                input_dict = {"input": str(lc_message)}
        else:
            input_dict = {"input": self.input_value}

        # 注意：确保 `input_dict` 已初始化
        if "input" not in input_dict:
            input_dict = {"input": self.input_value}

        # 注意：若存在增强系统提示（`IBM Granite` 设置），优先使用
        system_prompt_to_use = getattr(self, "_effective_system_prompt", None) or self.system_prompt
        if system_prompt_to_use and system_prompt_to_use.strip():
            input_dict["system_prompt"] = system_prompt_to_use

        if hasattr(self, "chat_history") and self.chat_history:
            if isinstance(self.chat_history, Data):
                input_dict["chat_history"] = self._data_to_messages_skip_empty([self.chat_history])
            elif all(hasattr(m, "to_data") and callable(m.to_data) and "text" in m.data for m in self.chat_history):
                input_dict["chat_history"] = self._data_to_messages_skip_empty(self.chat_history)
            elif all(isinstance(m, Message) for m in self.chat_history):
                input_dict["chat_history"] = self._data_to_messages_skip_empty([m.to_data() for m in self.chat_history])

        # 实现：处理多模态输入（图片+文本）
        # 注意：代理输入必须是字符串，图片转入 `chat_history`
        if lc_message is not None and hasattr(lc_message, "content") and isinstance(lc_message.content, list):
            # 实现：从内容项中拆分图片与文本
            # 注意：兼容 `image`（旧）与 `image_url`（标准）类型
            image_dicts = [item for item in lc_message.content if item.get("type") in ("image", "image_url")]
            text_content = [item for item in lc_message.content if item.get("type") not in ("image", "image_url")]

            text_strings = [
                item.get("text", "")
                for item in text_content
                if item.get("type") == "text" and item.get("text", "").strip()
            ]

            # 实现：输入设置为拼接文本或空字符串
            input_dict["input"] = " ".join(text_strings) if text_strings else ""

            # 注意：若输入仍为空或列表，提供默认提示
            if isinstance(input_dict["input"], list) or not input_dict["input"]:
                input_dict["input"] = "Process the provided images."

            if "chat_history" not in input_dict:
                input_dict["chat_history"] = []

            if isinstance(input_dict["chat_history"], list):
                input_dict["chat_history"].extend(HumanMessage(content=[image_dict]) for image_dict in image_dicts)
            else:
                input_dict["chat_history"] = [HumanMessage(content=[image_dict]) for image_dict in image_dicts]

        # 注意：最终兜底，避免空输入导致 `Anthropic API` 错误
        current_input = input_dict.get("input", "")
        if isinstance(current_input, list):
            current_input = " ".join(map(str, current_input))
        elif not isinstance(current_input, str):
            current_input = str(current_input)

        if not current_input.strip():
            input_dict["input"] = "Continue the conversation."
        else:
            input_dict["input"] = current_input

        if hasattr(self, "graph"):
            session_id = self.graph.session_id
        elif hasattr(self, "_session_id"):
            session_id = self._session_id
        else:
            session_id = None

        sender_name = get_chat_output_sender_name(self) or self.display_name or "AI"
        agent_message = Message(
            sender=MESSAGE_SENDER_AI,
            sender_name=sender_name,
            properties={"icon": "Bot", "state": "partial"},
            content_blocks=[ContentBlock(title="Agent Steps", contents=[])],
            session_id=session_id or uuid.uuid4(),
        )

        # 注意：若存在 `event_manager`，创建 `token` 回调
        # 实现：包装 `event_manager.on_token` 以匹配 `OnTokenFunctionType`
        on_token_callback: OnTokenFunctionType | None = None
        if self._event_manager:
            on_token_callback = cast("OnTokenFunctionType", self._event_manager.on_token)

        try:
            result = await process_agent_events(
                runnable.astream_events(
                    input_dict,
                    # 注意：`AgentExecutor` 会调用工具，因此使用共享回调
                    config={"callbacks": [AgentAsyncHandler(self.log), *self._get_shared_callbacks()]},
                    version="v2",
                ),
                agent_message,
                cast("SendMessageFunctionType", self.send_message),
                on_token_callback,
            )
        except ExceptionWithMessageError as e:
            # 注意：仅当消息已持久化（有 `ID`）时删除数据库记录
            if hasattr(e, "agent_message"):
                msg_id = e.agent_message.get_id()
                if msg_id:
                    await delete_message(id_=msg_id)
            await self._send_message_event(e.agent_message, category="remove_message")
            logger.error(f"ExceptionWithMessageError: {e}")
            raise
        except Exception as e:
            # 注意：记录其他异常并向上抛出
            logger.error(f"Error: {e}")
            raise

        self.status = result
        return result

    @abstractmethod
    def create_agent_runnable(self) -> Runnable:
        """创建代理可运行实例

        契约：
        - 输入：无
        - 输出：Runnable 实例
        - 副作用：无
        - 失败语义：如果创建失败，抛出相应异常
        """

    def validate_tool_names(self) -> None:
        """验证工具名称以确保它们符合所需模式

        契约：
        - 输入：无
        - 输出：无
        - 副作用：如果验证失败则抛出 ValueError
        - 失败语义：工具名称不符合要求的模式时抛出 ValueError
        """
        pattern = re.compile(r"^[a-zA-Z0-9_-]+$")
        if hasattr(self, "tools") and self.tools:
            for tool in self.tools:
                if not pattern.match(tool.name):
                    msg = (
                        f"Invalid tool name '{tool.name}': must only contain letters, numbers, underscores, dashes,"
                        " and cannot contain spaces."
                    )
                    raise ValueError(msg)


class LCToolsAgentComponent(LCAgentComponent):
    """工具型代理组件基类

    契约：
    - 输入：工具列表（可为空）与代理配置
    - 输出：可运行的代理执行器或可暴露为工具的代理
    - 副作用：验证工具名称并注入回调
    - 失败语义：工具名不合法抛 ValueError；构建失败抛异常
    """
    _base_inputs = [
        HandleInput(
            name="tools",
            display_name="Tools",
            input_types=["Tool"],
            is_list=True,
            required=False,
            info="These are the tools that the agent can use to help with tasks.",
        ),
        *LCAgentComponent.get_base_inputs(),
    ]

    def build_agent(self) -> AgentExecutor:
        """构建代理执行器（重写父类方法）

        契约：
        - 输入：无
        - 输出：AgentExecutor 实例
        - 副作用：验证工具名称
        - 失败语义：如果验证失败或代理构建失败，抛出相应异常
        """
        self.validate_tool_names()
        agent = self.create_agent_runnable()
        return AgentExecutor.from_agent_and_tools(
            agent=RunnableAgent(runnable=agent, input_keys_arg=["input"], return_keys_arg=["output"]),
            tools=self.tools,
            **self.get_agent_kwargs(flatten=True),
        )

    @abstractmethod
    def create_agent_runnable(self) -> Runnable:
        """创建代理可运行实例

        契约：
        - 输入：无
        - 输出：Runnable 实例
        - 副作用：无
        - 失败语义：如果创建失败，抛出相应异常
        """

    def get_tool_name(self) -> str:
        """获取工具名称

        契约：
        - 输入：无
        - 输出：工具名称字符串
        - 副作用：无
        - 失败语义：无
        """
        return self.display_name or "Agent"

    def get_tool_description(self) -> str:
        """获取工具描述

        契约：
        - 输入：无
        - 输出：工具描述字符串
        - 副作用：无
        - 失败语义：无
        """
        return self.agent_description or DEFAULT_TOOLS_DESCRIPTION

    def _build_tools_names(self):
        """构建工具名称字符串

        契约：
        - 输入：无
        - 输出：包含所有工具名称的字符串
        - 副作用：无
        - 失败语义：无
        """
        tools_names = ""
        if self.tools:
            tools_names = ", ".join([tool.name for tool in self.tools])
        return tools_names

    # 注意：为工具设置共享回调以便追踪
    def set_tools_callbacks(self, tools_list: list[Tool], callbacks_list: list[BaseCallbackHandler]):
        """为工具设置共享回调处理器

        契约：
        - 输入：工具列表和回调处理器列表
        - 输出：无
        - 副作用：为每个工具设置回调处理器
        - 失败语义：无
        """
        for tool in tools_list or []:
            if hasattr(tool, "callbacks"):
                tool.callbacks = callbacks_list

    async def _get_tools(self) -> list[Tool]:
        """获取工具列表

        契约：
        - 输入：无
        - 输出：Tool 对象列表
        - 副作用：使用组件工具包创建工具
        - 失败语义：如果工具创建失败，抛出相应异常
        """
        component_toolkit = _get_component_toolkit()
        tools_names = self._build_tools_names()
        agent_description = self.get_tool_description()
        # 注意：`agent_description` 已废弃，后续移除
        description = f"{agent_description}{tools_names}"

        tools = component_toolkit(component=self).get_tools(
            tool_name=self.get_tool_name(),
            tool_description=description,
            # 注意：代理作为工具暴露时不使用共享回调
            callbacks=self.get_langchain_callbacks(),
        )
        if hasattr(self, "tools_metadata"):
            tools = component_toolkit(component=self, metadata=self.tools_metadata).update_tools_metadata(tools=tools)

        return tools
