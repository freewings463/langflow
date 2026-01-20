"""
模块名称：`ALTK` 代理基类与工具包装框架

本模块提供 `ALTK` 代理组件的基础实现与工具包装管线，主要用于将 `LangChain` 代理与工具
映射到 `ALTK` 运行时并统一事件处理。
主要功能包括：
- 消息规范化与输入清洗
- 工具包装与 LLM 提取适配
- 代理执行、事件流处理与异常语义

关键组件：
- `BaseToolWrapper`：工具包装器抽象
- `ALTKBaseTool`：具备 ALTK LLM 访问能力的工具基类
- `ToolPipelineManager`：包装器链路管理
- `ALTKBaseAgentComponent`：ALTK 代理组件基类

设计背景：`ALTK` 与 `LangChain` 对象协议不一致，需要统一适配层。
注意事项：包装器链路受最大深度限制；LLM 类型不匹配时返回 None。
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, cast

from altk.core.llm import get_llm
from langchain.agents import AgentExecutor, BaseMultiActionAgent, BaseSingleActionAgent
from langchain_anthropic.chat_models import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.runnables import Runnable, RunnableBinding
from langchain_core.tools import BaseTool
from langchain_openai.chat_models.base import ChatOpenAI
from pydantic import Field

from lfx.base.agents.callback import AgentAsyncHandler
from lfx.base.agents.events import ExceptionWithMessageError, process_agent_events
from lfx.base.agents.utils import data_to_messages, get_chat_output_sender_name
from lfx.components.models_and_agents import AgentComponent
from lfx.log.logger import logger
from lfx.memory import delete_message
from lfx.schema.content_block import ContentBlock
from lfx.schema.data import Data

if TYPE_CHECKING:
    from collections.abc import Sequence

    from lfx.schema.log import SendMessageFunctionType

from lfx.schema.message import Message
from lfx.utils.constants import MESSAGE_SENDER_AI


def normalize_message_content(message: BaseMessage) -> str:
    """标准化消息内容以处理来自 Data.to_lc_message() 的不一致格式

    关键路径（三步）：
    1) 检查消息内容是否为字符串格式
    2) 检查消息内容是否为列表格式并提取文本
    3) 返回提取的文本内容或空字符串

    异常流：消息内容格式不符合预期时返回字符串形式的内容。
    性能瓶颈：无显著性能瓶颈。
    排障入口：日志关键字 "normalize_message_content"。
    
    契约：
    - 输入：BaseMessage 对象
    - 输出：提取的文本内容字符串
    - 副作用：无
    - 失败语义：返回字符串形式的内容
    """
    content = message.content

    # 处理字符串格式（`AI` 消息）
    if isinstance(content, str):
        return content

    # 处理列表格式（用户消息）
    if isinstance(content, list) and len(content) > 0:
        # 实现：提取首个包含 `text` 字段的内容
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text" and "text" in item:
                return item["text"]
        # 注意：未找到文本时返回空字符串（如仅包含图片）
        return ""

    # 处理空列表或其他格式
    if isinstance(content, list):
        return ""

    # 兜底处理其他格式
    return str(content)


# === 工具包装架构 ===


class BaseToolWrapper(ABC):
    """工具包装器管道中的所有工具的基类

    工具包装器可以通过添加执行前验证、执行后处理或其他功能来增强工具。
    
    契约：
    - 输入：BaseTool 对象及额外参数
    - 输出：包装后的 BaseTool 对象
    - 副作用：根据具体实现而定
    - 失败语义：如果包装失败，抛出相应异常
    """

    @abstractmethod
    def wrap_tool(self, tool: BaseTool, **kwargs) -> BaseTool:
        """用增强功能包装工具

        契约：
        - 输入：BaseTool 对象和关键字参数
        - 输出：包装后的 BaseTool 对象
        - 副作用：根据具体包装器实现而定
        - 失败语义：如果包装失败，抛出相应异常
        """

    def initialize(self, **_kwargs) -> bool:  # pragma: no cover - trivial
        """初始化包装器所需的任何资源

        契约：
        - 输入：关键字参数
        - 输出：布尔值表示初始化是否成功
        - 副作用：无
        - 失败语义：始终返回 True
        """
        return True

    @property
    def is_available(self) -> bool:  # pragma: no cover - trivial
        """检查包装器是否可用

        契约：
        - 输入：无
        - 输出：布尔值表示是否可用
        - 副作用：无
        - 失败语义：始终返回 True
        """
        return True


class ALTKBaseTool(BaseTool):
    """需要代理交互和 ALTK LLM 访问的工具的基类

    为工具执行和 ALTK LLM 对象创建提供通用功能。
    
    契约：
    - 输入：工具名称、描述、被包装的工具和代理
    - 输出：ALTKBaseTool 实例
    - 副作用：继承自 BaseTool 的功能
    - 失败语义：如果执行失败，抛出相应异常
    """

    name: str = Field(...)
    description: str = Field(...)
    wrapped_tool: BaseTool = Field(...)
    agent: Runnable | BaseSingleActionAgent | BaseMultiActionAgent | AgentExecutor = Field(...)

    def _run(self, *args, **kwargs) -> str:
        """使用包装的工具执行的抽象方法实现

        契约：
        - 输入：位置参数和关键字参数
        - 输出：执行结果字符串
        - 副作用：执行包装的工具
        - 失败语义：如果执行失败，抛出相应异常
        """
        return self._execute_tool(*args, **kwargs)

    def _execute_tool(self, *args, **kwargs) -> str:
        """使用跨 LC 版本的兼容性执行包装的工具

        契约：
        - 输入：位置参数和关键字参数
        - 输出：工具执行结果字符串
        - 副作用：执行被包装的工具
        - 失败语义：如果执行失败，抛出相应异常
        """
        # 注意：`BaseTool.run()` 期望 `tool_input` 为第一个参数
        if args:
            # 实现：首参作为 `tool_input`，其余参数透传
            tool_input = args[0]
            return self.wrapped_tool.run(tool_input, *args[1:])
        if kwargs:
            # 实现：将 `kwargs` 作为 `tool_input`
            return self.wrapped_tool.run(kwargs)
        # 注意：无参数时使用空字典作为 `tool_input`
        return self.wrapped_tool.run({})

    def _get_altk_llm_object(self, *, use_output_val: bool = True) -> Any:
        """提取底层 LLM 并将其映射到 ALTK 客户端对象

        契约：
        - 输入：use_output_val 参数决定是否使用输出值
        - 输出：ALTK LLM 客户端对象或 None
        - 副作用：访问代理的步骤以查找 LLM 对象
        - 失败语义：如果不支持的模型类型，返回 None
        """
        llm_object: BaseChatModel | None = None
        steps = getattr(self.agent, "steps", None)
        if steps:
            for step in steps:
                if isinstance(step, RunnableBinding) and isinstance(step.bound, BaseChatModel):
                    llm_object = step.bound
                    break

        if isinstance(llm_object, ChatAnthropic):
            model_name = f"anthropic/{llm_object.model}"
            api_key = llm_object.anthropic_api_key.get_secret_value()
            llm_client_type = "litellm.output_val" if use_output_val else "litellm"
            llm_client = get_llm(llm_client_type)
            llm_client_obj = llm_client(model_name=model_name, api_key=api_key)
        elif isinstance(llm_object, ChatOpenAI):
            model_name = llm_object.model_name
            api_key = llm_object.openai_api_key.get_secret_value()
            llm_client_type = "openai.sync.output_val" if use_output_val else "openai.sync"
            llm_client = get_llm(llm_client_type)
            llm_client_obj = llm_client(model=model_name, api_key=api_key)
        else:
            logger.info("ALTK currently only supports OpenAI and Anthropic models through Langflow.")
            llm_client_obj = None

        return llm_client_obj


class ToolPipelineManager:
    """管理工具包装器序列并将它们应用于工具"""

    def __init__(self):
        """初始化工具管道管理器

        契约：
        - 输入：无
        - 输出：ToolPipelineManager 实例
        - 副作用：初始化包装器列表
        - 失败语义：无
        """
        self.wrappers: list[BaseToolWrapper] = []

    def clear(self) -> None:
        """清空包装器列表

        契约：
        - 输入：无
        - 输出：无
        - 副作用：清空包装器列表
        - 失败语义：无
        """
        self.wrappers.clear()

    def add_wrapper(self, wrapper: BaseToolWrapper) -> None:
        """添加包装器到列表

        契约：
        - 输入：BaseToolWrapper 实例
        - 输出：无
        - 副作用：将包装器添加到列表
        - 失败语义：无
        """
        self.wrappers.append(wrapper)

    def configure_wrappers(self, wrappers: list[BaseToolWrapper]) -> None:
        """用新配置替换当前包装器

        契约：
        - 输入：BaseToolWrapper 列表
        - 输出：无
        - 副作用：清空当前包装器并添加新包装器
        - 失败语义：如果配置失败，抛出相应异常
        """
        self.clear()
        for wrapper in wrappers:
            self.add_wrapper(wrapper)

    def process_tools(self, tools: list[BaseTool], **kwargs) -> list[BaseTool]:
        """处理工具列表，应用包装器

        契约：
        - 输入：BaseTool 列表和关键字参数
        - 输出：处理后的 BaseTool 列表
        - 副作用：应用包装器到每个工具
        - 失败语义：如果处理失败，抛出相应异常
        """
        return [self._apply_wrappers_to_tool(tool, **kwargs) for tool in tools]

    def _apply_wrappers_to_tool(self, tool: BaseTool, **kwargs) -> BaseTool:
        """将包装器应用于单个工具

        契约：
        - 输入：BaseTool 实例和关键字参数
        - 输出：包装后的 BaseTool 实例
        - 副作用：按相反顺序应用包装器
        - 失败语义：如果应用失败，抛出相应异常
        """
        wrapped_tool = tool
        for wrapper in reversed(self.wrappers):
            if wrapper.is_available:
                wrapped_tool = wrapper.wrap_tool(wrapped_tool, **kwargs)
        return wrapped_tool


# === 代理组件编排 ===


class ALTKBaseAgentComponent(AgentComponent):
    """集中编排和钩子的基代理组件

    子类应重写 `get_tool_wrappers` 以提供其包装器，
    并可根据需要自定义上下文构建。
    
    关键路径（三步）：
    1) 初始化组件和工具管道管理器
    2) 构建对话上下文和配置工具管道
    3) 运行代理并处理结果
    
    异常流：工具管道配置失败、对话上下文构建失败、代理执行异常。
    性能瓶颈：工具包装器处理、复杂对话历史处理。
    排障入口：日志关键字 "configure_tool_pipeline"、"build_conversation_context"。
    """

    def __init__(self, **kwargs):
        """初始化 ALTK 基础代理组件

        契约：
        - 输入：关键字参数
        - 输出：ALTKBaseAgentComponent 实例
        - 副作用：初始化父类和工具管道管理器
        - 失败语义：如果初始化失败，抛出相应异常
        """
        super().__init__(**kwargs)
        self.pipeline_manager = ToolPipelineManager()

    # ---- 子类扩展钩子 ----
    def configure_tool_pipeline(self) -> None:
        """配置工具管道与包装器。子类重写此方法。

        契约：
        - 输入：无
        - 输出：无
        - 副作用：配置工具管道管理器
        - 失败语义：如果配置失败，抛出相应异常
        """
        # 注意：默认不启用包装器
        self.pipeline_manager.clear()

    def build_conversation_context(self) -> list[BaseMessage]:
        """从输入和聊天历史创建对话上下文

        契约：
        - 输入：无
        - 输出：BaseMessage 对象列表
        - 副作用：按时间顺序组织消息
        - 失败语义：如果构建失败，抛出相应异常
        """
        context: list[BaseMessage] = []

        # 注意：先加入历史对话以保持时间顺序
        if hasattr(self, "chat_history") and self.chat_history:
            if isinstance(self.chat_history, Data):
                context.append(self.chat_history.to_lc_message())
            elif isinstance(self.chat_history, list):
                if all(isinstance(m, Message) for m in self.chat_history):
                    context.extend([m.to_lc_message() for m in self.chat_history])
                else:
                    # 注意：假定为 `Data` 列表，交由 `data_to_messages` 验证
                    try:
                        context.extend(data_to_messages(self.chat_history))
                    except (AttributeError, TypeError) as e:
                        error_message = f"Invalid chat_history list contents: {e}"
                        raise ValueError(error_message) from e
            else:
                # 注意：拒绝其他类型（字符串、数字等）
                type_name = type(self.chat_history).__name__
                error_message = (
                    f"chat_history must be a Data object, list of Data/Message objects, or None. Got: {type_name}"
                )
                raise ValueError(error_message)

        # 注意：再加入当前输入以保持时间顺序
        if hasattr(self, "input_value") and self.input_value:
            if isinstance(self.input_value, Message):
                context.append(self.input_value.to_lc_message())
            else:
                context.append(HumanMessage(content=str(self.input_value)))

        return context

    def get_user_query(self) -> str:
        """获取用户查询

        契约：
        - 输入：无
        - 输出：用户查询字符串
        - 副作用：无
        - 失败语义：如果获取失败，返回字符串形式的输入值
        """
        if hasattr(self.input_value, "get_text") and callable(self.input_value.get_text):
            return self.input_value.get_text()
        return str(self.input_value)

    # ---- `run`/`update` 复用的内部辅助 ----
    def _initialize_tool_pipeline(self) -> None:
        """通过调用子类配置初始化工具管道

        契约：
        - 输入：无
        - 输出：无
        - 副作用：调用子类的配置方法
        - 失败语义：如果初始化失败，抛出相应异常
        """
        self.configure_tool_pipeline()

    def update_runnable_instance(
        self, agent: AgentExecutor, runnable: AgentExecutor, tools: Sequence[BaseTool]
    ) -> AgentExecutor:
        """使用处理后的工具更新可运行实例

        子类可以重写此方法以自定义工具处理。
        默认实现应用工具包装器管道。
        
        契约：
        - 输入：代理、可运行实例和工具序列
        - 输出：更新后的 AgentExecutor 实例
        - 副作用：处理并替换可运行实例中的工具
        - 失败语义：如果更新失败，抛出相应异常
        """
        user_query = self.get_user_query()
        conversation_context = self.build_conversation_context()

        self._initialize_tool_pipeline()
        processed_tools = self.pipeline_manager.process_tools(
            list(tools or []),
            agent=agent,
            user_query=user_query,
            conversation_context=conversation_context,
        )

        runnable.tools = processed_tools
        return runnable

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
        
        契约：
        - 输入：各种类型的代理实例
        - 输出：Message 对象
        - 副作用：设置 self.status 为结果消息
        - 失败语义：如果代理运行失败，抛出相应异常
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
        runnable = self.update_runnable_instance(agent, runnable, self.tools)

        # 实现：将 `input_value` 规范化为代理可接受的输入
        if hasattr(self.input_value, "to_lc_message") and callable(self.input_value.to_lc_message):
            lc_message = self.input_value.to_lc_message()
            input_text = lc_message.content if hasattr(lc_message, "content") else str(lc_message)
        else:
            lc_message = None
            input_text = self.input_value

        input_dict: dict[str, str | list[BaseMessage]] = {}
        if hasattr(self, "system_prompt"):
            input_dict["system_prompt"] = self.system_prompt
        if hasattr(self, "chat_history") and self.chat_history:
            if (
                hasattr(self.chat_history, "to_data")
                and callable(self.chat_history.to_data)
                and self.chat_history.__class__.__name__ == "Data"
            ):
                input_dict["chat_history"] = data_to_messages(self.chat_history)
            # 注意：兼容 `lfx.schema.message.Message` 与 `langflow.schema.message.Message`
            if all(hasattr(m, "to_data") and callable(m.to_data) and "text" in m.data for m in self.chat_history):
                input_dict["chat_history"] = data_to_messages(self.chat_history)
            if all(isinstance(m, Message) for m in self.chat_history):
                input_dict["chat_history"] = data_to_messages([m.to_data() for m in self.chat_history])
        if hasattr(lc_message, "content") and isinstance(lc_message.content, list):
            # 注意：输入必须是字符串，图片需转入 `chat_history`
            # 注意：兼容 `image`（旧）与 `image_url`（标准）类型
            image_dicts = [item for item in lc_message.content if item.get("type") in ("image", "image_url")]
            lc_message.content = [item for item in lc_message.content if item.get("type") not in ("image", "image_url")]

            if "chat_history" not in input_dict:
                input_dict["chat_history"] = []
            if isinstance(input_dict["chat_history"], list):
                input_dict["chat_history"].extend(HumanMessage(content=[image_dict]) for image_dict in image_dicts)
            else:
                input_dict["chat_history"] = [HumanMessage(content=[image_dict]) for image_dict in image_dicts]
        input_dict["input"] = input_text

        # 注意：与 `agent.py` 保持一致的兜底逻辑
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

        try:
            sender_name = get_chat_output_sender_name(self)
        except AttributeError:
            sender_name = self.display_name or "AI"

        agent_message = Message(
            sender=MESSAGE_SENDER_AI,
            sender_name=sender_name,
            properties={"icon": "Bot", "state": "partial"},
            content_blocks=[ContentBlock(title="Agent Steps", contents=[])],
            session_id=session_id or uuid.uuid4(),
        )
        try:
            result = await process_agent_events(
                runnable.astream_events(
                    input_dict,
                    config={
                        "callbacks": [
                            AgentAsyncHandler(self.log),
                            *self.get_langchain_callbacks(),
                        ]
                    },
                    version="v2",
                ),
                agent_message,
                cast("SendMessageFunctionType", self.send_message),
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
