"""
模块名称：Cuga Agent 组件

本模块提供 Cuga Agent 的组件封装，主要用于将 Cuga 任务执行流程
接入 LFX 的工具调用与事件处理体系。
主要功能包括：
- 构建 Cuga Agent 并驱动任务执行（含事件流）
- 统一模型、记忆与工具的装配
- 动态更新构建配置与模型提供方字段

关键组件：
- `CugaComponent`：Cuga 任务执行组件

设计背景：在 LFX 中复用 Cuga 的任务编排能力并保持一致的组件体验。
注意事项：依赖外部 Cuga/Playwright 等可选依赖，缺失会导致运行时错误。
"""

import asyncio
import json
import traceback
import uuid
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, cast

from langchain_core.agents import AgentFinish
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool

from lfx.base.agents.agent import LCToolsAgentComponent
from lfx.base.models.model_input_constants import (
    ALL_PROVIDER_FIELDS,
    MODEL_DYNAMIC_UPDATE_FIELDS,
    MODEL_PROVIDERS,
    MODEL_PROVIDERS_DICT,
    MODELS_METADATA,
)
from lfx.base.models.model_utils import get_model_name
from lfx.components.helpers import CurrentDateComponent
from lfx.components.langchain_utilities.tool_calling import ToolCallingAgentComponent
from lfx.components.models_and_agents.memory import MemoryComponent
from lfx.custom.custom_component.component import _get_component_toolkit
from lfx.custom.utils import update_component_build_config
from lfx.field_typing import Tool
from lfx.io import BoolInput, DropdownInput, IntInput, MultilineInput, Output
from lfx.log.logger import logger
from lfx.schema.dotdict import dotdict
from lfx.schema.message import Message

if TYPE_CHECKING:
    from lfx.schema.log import SendMessageFunctionType


def set_advanced_true(component_input):
    """将输入字段标记为高级选项。

    契约：返回被修改后的输入对象。
    副作用：就地修改 `component_input.advanced`。
    """
    component_input.advanced = True
    return component_input


MODEL_PROVIDERS_LIST = ["OpenAI"]


class CugaComponent(ToolCallingAgentComponent):
    """Cuga Agent 组件封装。

    契约：基于配置构建 LLM、记忆与工具并输出事件流响应。
    副作用：可能触发外部浏览器/网络调用并写入消息流状态。
    失败语义：初始化或执行失败会抛 `ValueError` 并在日志中记录。
    决策：以 ToolCallingAgent 事件流对接 Cuga 执行结果
    问题：需要与 LFX 统一的事件与消息处理机制对接
    方案：在 `message_response` 中使用 `process_agent_events` 处理流式事件
    代价：事件语义需与 LFX 事件处理器保持一致
    重评：当 Cuga 事件协议变化时需要同步调整
    """

    display_name: str = "Cuga"
    description: str = "Define the Cuga agent's instructions, then assign it a task."
    documentation: str = "https://docs.langflow.org/bundles-cuga"
    icon = "bot"
    name = "Cuga"

    memory_inputs = [set_advanced_true(component_input) for component_input in MemoryComponent().inputs]

    inputs = [
        DropdownInput(
            name="agent_llm",
            display_name="Model Provider",
            info="The provider of the language model that the agent will use to generate responses.",
            options=[*MODEL_PROVIDERS_LIST, "Custom"],
            value="OpenAI",
            real_time_refresh=True,
            input_types=[],
            options_metadata=[MODELS_METADATA[key] for key in MODEL_PROVIDERS_LIST] + [{"icon": "brain"}],
        ),
        *MODEL_PROVIDERS_DICT["OpenAI"]["inputs"],
        MultilineInput(
            name="instructions",
            display_name="Instructions",
            info=(
                "Custom instructions for the agent to adhere to during its operation.\n"
                "Example:\n"
                "## Plan\n"
                "< planning instructions e.g. which tools and when to use>\n"
                "## Answer\n"
                "< final answer instructions how to answer>"
            ),
            value="",
            advanced=False,
        ),
        IntInput(
            name="n_messages",
            display_name="Number of Chat History Messages",
            value=100,
            info="Number of chat history messages to retrieve.",
            advanced=True,
            show=True,
        ),
        *LCToolsAgentComponent.get_base_inputs(),
        BoolInput(
            name="add_current_date_tool",
            display_name="Current Date",
            advanced=True,
            info="If true, will add a tool to the agent that returns the current date.",
            value=True,
        ),
        BoolInput(
            name="lite_mode",
            display_name="Enable CugaLite",
            info="Faster reasoning for simple tasks. Enable CugaLite for simple API tasks.",
            value=True,
            advanced=True,
        ),
        IntInput(
            name="lite_mode_tool_threshold",
            display_name="CugaLite Tool Threshold",
            info="Route to CugaLite if app has fewer than this many tools.",
            value=25,
            advanced=True,
        ),
        DropdownInput(
            name="decomposition_strategy",
            display_name="Decomposition Strategy",
            info="Strategy for task decomposition: 'flexible' allows multiple subtasks per app,\n"
            " 'exact' enforces one subtask per app.",
            options=["flexible", "exact"],
            value="flexible",
            advanced=True,
        ),
        BoolInput(
            name="browser_enabled",
            display_name="Enable Browser",
            info="Toggle to enable a built-in browser tool for web scraping and searching.",
            value=False,
            advanced=True,
        ),
        MultilineInput(
            name="web_apps",
            display_name="Web applications",
            info=(
                "Cuga will automatically start this web application when Enable Browser is true. "
                "Currently only supports one web application. Example: https://example.com"
            ),
            value="",
            advanced=True,
        ),
    ]
    outputs = [
        Output(name="response", display_name="Response", method="message_response"),
    ]

    async def call_agent(
        self, current_input: str, tools: list[Tool], history_messages: list[Message], llm
    ) -> AsyncIterator[dict[str, Any]]:
        """执行 Cuga 任务并以事件流形式输出。

        关键路径（三步）：
        1) 配置 Cuga settings 与构建运行环境
        2) 将历史消息转换为 LangChain 消息并启动任务
        3) 将 Cuga 事件映射为 LFX 事件并持续 yield
        异常流：初始化/执行异常会 emit `on_chain_error` 事件。
        性能瓶颈：外部模型调用与工具执行时延。
        排障入口：关注 `[CUGA]` 日志与 event name。
        """
        yield {
            "event": "on_chain_start",
            "run_id": str(uuid.uuid4()),
            "name": "CUGA_initializing",
            "data": {"input": {"input": current_input, "chat_history": []}},
        }
        logger.debug(f"[CUGA] LLM MODEL TYPE: {type(llm)}")
        if current_input:
            # 注意：先加载 settings 以便动态更新配置
            from cuga.config import settings

            # 决策：使用 Dynaconf 的属性赋值以避免配置对象损坏
            logger.debug("[CUGA] Updating CUGA settings via Dynaconf set() method")

            settings.advanced_features.registry = False
            settings.advanced_features.lite_mode = self.lite_mode
            settings.advanced_features.lite_mode_tool_threshold = self.lite_mode_tool_threshold
            settings.advanced_features.decomposition_strategy = self.decomposition_strategy

            if self.browser_enabled:
                logger.debug("[CUGA] browser_enabled is true, setting mode to hybrid")
                settings.advanced_features.mode = "hybrid"
                settings.advanced_features.use_vision = False
            else:
                logger.debug("[CUGA] browser_enabled is false, setting mode to api")
                settings.advanced_features.mode = "api"

            from cuga.backend.activity_tracker.tracker import ActivityTracker
            from cuga.backend.cuga_graph.utils.agent_loop import StreamEvent
            from cuga.backend.cuga_graph.utils.controller import (
                AgentRunner as CugaAgent,
            )
            from cuga.backend.cuga_graph.utils.controller import (
                ExperimentResult as AgentResult,
            )
            from cuga.backend.llm.models import LLMManager
            from cuga.configurations.instructions_manager import InstructionsManager

            # 注意：首条对话会重置内部状态
            logger.debug(f"[CUGA] Checking history_messages: count={len(history_messages) if history_messages else 0}")
            if not history_messages or len(history_messages) == 0:
                logger.debug("[CUGA] First message in history detected, resetting var_manager")
            else:
                logger.debug(f"[CUGA] Continuing conversation with {len(history_messages)} previous messages")

            llm_manager = LLMManager()
            llm_manager.set_llm(llm)
            instructions_manager = InstructionsManager()

            instructions_to_use = self.instructions or ""
            logger.debug(f"[CUGA] instructions are: {instructions_to_use}")
            instructions_manager.set_instructions_from_one_file(instructions_to_use)
            tracker = ActivityTracker()
            tracker.set_tools(tools)
            thread_id = self.graph.session_id
            logger.debug(f"[CUGA] Using thread_id (session_id): {thread_id}")
            cuga_agent = CugaAgent(browser_enabled=self.browser_enabled, thread_id=thread_id)
            if self.browser_enabled:
                await cuga_agent.initialize_freemode_env(start_url=self.web_apps.strip(), interface_mode="browser_only")
            else:
                await cuga_agent.initialize_appworld_env()

            yield {
                "event": "on_chain_start",
                "run_id": str(uuid.uuid4()),
                "name": "CUGA_thinking...",
                "data": {"input": {"input": current_input, "chat_history": []}},
            }
            logger.debug(f"[CUGA] current web apps are {self.web_apps}")
            logger.debug(f"[CUGA] Processing input: {current_input}")
            try:
                # 实现：将历史消息转换为 LangChain 消息格式
                logger.debug(f"[CUGA] Converting {len(history_messages)} history messages to LangChain format")
                lc_messages = []
                for i, msg in enumerate(history_messages):
                    msg_text = getattr(msg, "text", "N/A")[:50] if hasattr(msg, "text") else "N/A"
                    logger.debug(
                        f"[CUGA] Message {i}: type={type(msg)}, sender={getattr(msg, 'sender', 'N/A')}, "
                        f"text={msg_text}..."
                    )
                    if hasattr(msg, "sender") and msg.sender == "Human":
                        lc_messages.append(HumanMessage(content=msg.text))
                    else:
                        lc_messages.append(AIMessage(content=msg.text))

                logger.debug(f"[CUGA] Converted to {len(lc_messages)} LangChain messages")
                await asyncio.sleep(0.5)

                # 实现：组装响应占位信息
                response_parts = []

                response_parts.append(f"Processed input: '{current_input}'")
                response_parts.append(f"Available tools: {len(tools)}")
                last_event: StreamEvent | None = None
                tool_run_id: str | None = None
                # 实现：任务完成后触发链结束事件
                async for event in cuga_agent.run_task_generic_yield(
                    eval_mode=False, goal=current_input, chat_messages=lc_messages
                ):
                    logger.debug(f"[CUGA] recieved event {event}")
                    if last_event is not None and tool_run_id is not None:
                        logger.debug(f"[CUGA] last event {last_event}")
                        try:
                            # 注意：TODO 统一事件数据结构
                            data_dict = json.loads(last_event.data)
                        except json.JSONDecodeError:
                            data_dict = last_event.data
                        if last_event.name == "CodeAgent":
                            data_dict = data_dict["code"]
                        yield {
                            "event": "on_tool_end",
                            "run_id": tool_run_id,
                            "name": last_event.name,
                            "data": {"output": data_dict},
                        }
                    if isinstance(event, StreamEvent):
                        tool_run_id = str(uuid.uuid4())
                        last_event = StreamEvent(name=event.name, data=event.data)
                        tool_event = {
                            "event": "on_tool_start",
                            "run_id": tool_run_id,
                            "name": event.name,
                            "data": {"input": {}},
                        }
                        logger.debug(f"[CUGA] Yielding tool_start event: {event.name}")
                        yield tool_event

                    if isinstance(event, AgentResult):
                        task_result = event
                        end_event = {
                            "event": "on_chain_end",
                            "run_id": str(uuid.uuid4()),
                            "name": "CugaAgent",
                            "data": {"output": AgentFinish(return_values={"output": task_result.answer}, log="")},
                        }
                        answer_preview = task_result.answer[:100] if task_result.answer else "None"
                        logger.info(f"[CUGA] Yielding chain_end event with answer: {answer_preview}...")
                        yield end_event

            except (ValueError, TypeError, RuntimeError, ConnectionError) as e:
                logger.error(f"[CUGA] An error occurred: {e!s}")
                logger.error(f"[CUGA] Traceback: {traceback.format_exc()}")
                error_msg = f"CUGA Agent error: {e!s}"
                logger.error(f"[CUGA] Error occurred: {error_msg}")

                # 实现：发送错误事件
                yield {
                    "event": "on_chain_error",
                    "run_id": str(uuid.uuid4()),
                    "name": "CugaAgent",
                    "data": {"error": error_msg},
                }

    async def message_response(self) -> Message:
        """通过 Cuga 执行任务并返回最终消息。

        关键路径（三步）：
        1) 校验输入并准备 LLM/记忆/工具
        2) 构建事件流并交给 `process_agent_events` 处理
        3) 返回最终 `Message` 结果
        异常流：执行异常会抛出并记录日志；缺少 Playwright 提示安装方式。
        排障入口：关注 `[CUGA]` 日志与消息状态字段。
        """
        logger.debug("[CUGA] Starting Cuga agent run for message_response.")
        logger.debug(f"[CUGA] Agent input value: {self.input_value}")

        # 注意：输入不能为空
        if not self.input_value or not str(self.input_value).strip():
            msg = "Message cannot be empty. Please provide a valid message."
            raise ValueError(msg)

        try:
            from lfx.schema.content_block import ContentBlock
            from lfx.schema.message import MESSAGE_SENDER_AI

            llm_model, self.chat_history, self.tools = await self.get_agent_requirements()

            # 实现：构建用于事件处理的消息载体
            agent_message = Message(
                sender=MESSAGE_SENDER_AI,
                sender_name="Cuga",
                properties={"icon": "Bot", "state": "partial"},
                content_blocks=[ContentBlock(title="Agent Steps", contents=[])],
                session_id=self.graph.session_id,
            )

            # 实现：预分配 ID，确保未连接 ChatOutput 时也能流式更新
            if not self.is_connected_to_chat_output():
                agent_message.data["id"] = uuid.uuid4()

            # 实现：读取输入文本
            input_text = self.input_value.text if hasattr(self.input_value, "text") else str(self.input_value)

            # 实现：构建事件迭代器
            event_iterator = self.call_agent(
                current_input=input_text, tools=self.tools or [], history_messages=self.chat_history, llm=llm_model
            )

            # 实现：使用统一事件处理器消费事件
            from lfx.base.agents.events import process_agent_events

            # 注意：强制写库以便轮询 UI 实时可见
            async def force_db_update_send_message(message, id_=None, *, skip_db_update=False):  # noqa: ARG001
                content_blocks_len = len(message.content_blocks[0].contents) if message.content_blocks else 0
                logger.debug(
                    f"[CUGA] Sending message update - state: {message.properties.state}, "
                    f"content_blocks: {content_blocks_len}"
                )

                result = await self.send_message(message, id_=id_, skip_db_update=False)

                logger.debug(f"[CUGA] Message processed with ID: {result.id}")
                return result

            result = await process_agent_events(
                event_iterator, agent_message, cast("SendMessageFunctionType", force_db_update_send_message)
            )

            logger.debug("[CUGA] Agent run finished successfully.")
            logger.debug(f"[CUGA] Agent output: {result}")

        except Exception as e:
            logger.error(f"[CUGA] Error in message_response: {e}")
            logger.error(f"[CUGA] An error occurred: {e!s}")
            logger.error(f"[CUGA] Traceback: {traceback.format_exc()}")

            # 排障：针对 Playwright 未安装的错误提示
            error_str = str(e).lower()
            if "playwright install" in error_str:
                msg = (
                    "Playwright is not installed. Please install Playwright Chromium using: "
                    "uv run -m playwright install chromium"
                )
                raise ValueError(msg) from e

            raise
        else:
            return result

    async def get_agent_requirements(self):
        """获取 Cuga 运行所需的模型、记忆与工具。

        关键路径（三步）：
        1) 构建/获取 LLM 并记录模型名
        2) 拉取会话历史并规范化为列表
        3) 按配置追加当前日期工具
        异常流：未选模型或模型初始化失败会抛 `ValueError`。
        """
        llm_model, display_name = await self.get_llm()
        if llm_model is None:
            msg = "No language model selected. Please choose a model to proceed."
            raise ValueError(msg)
        self.model_name = get_model_name(llm_model, display_name=display_name)

        # 实现：获取记忆数据
        self.chat_history = await self.get_memory_data()
        if isinstance(self.chat_history, Message):
            self.chat_history = [self.chat_history]

        # 实现：按需追加当前日期工具
        if self.add_current_date_tool:
            if not isinstance(self.tools, list):
                self.tools = []
            current_date_tool = (await CurrentDateComponent(**self.get_base_args()).to_toolkit()).pop(0)
            if not isinstance(current_date_tool, StructuredTool):
                msg = "CurrentDateComponent must be converted to a StructuredTool"
                raise TypeError(msg)
            self.tools.append(current_date_tool)

        # 排障：调试日志开始
        logger.debug("[CUGA] Retrieved agent requirements: LLM, chat history, and tools.")
        logger.debug(f"[CUGA] LLM model: {self.model_name}")
        logger.debug(f"[CUGA] Number of chat history messages: {len(self.chat_history)}")
        logger.debug(f"[CUGA] Tools available: {[tool.name for tool in self.tools]}")
        logger.debug(f"[CUGA] metadata: {[tool.metadata for tool in self.tools]}")
        # 排障：调试日志结束

        return llm_model, self.chat_history, self.tools

    async def get_memory_data(self):
        """读取历史消息并排除当前输入消息。

        契约：返回 `Message` 列表，不包含当前输入消息。
        副作用：读取外部记忆存储。
        """
        logger.debug("[CUGA] Retrieving chat history messages.")
        logger.debug(f"[CUGA] Session ID: {self.graph.session_id}")
        logger.debug(f"[CUGA] n_messages: {self.n_messages}")
        logger.debug(f"[CUGA] input_value: {self.input_value}")
        logger.debug(f"[CUGA] input_value type: {type(self.input_value)}")
        logger.debug(f"[CUGA] input_value id: {getattr(self.input_value, 'id', None)}")

        messages = (
            await MemoryComponent(**self.get_base_args())
            .set(session_id=str(self.graph.session_id), order="Ascending", n_messages=self.n_messages)
            .retrieve_messages()
        )
        logger.debug(f"[CUGA] Retrieved {len(messages)} messages from memory")
        return [
            message for message in messages if getattr(message, "id", None) != getattr(self.input_value, "id", None)
        ]

    async def get_llm(self):
        """根据当前配置获取 LLM 实例。

        关键路径（三步）：
        1) 解析提供方并读取其输入配置
        2) 组装参数并构建模型实例
        3) 返回模型与显示名
        异常流：提供方无效或构建失败抛 `ValueError`。
        """
        logger.debug("[CUGA] Getting language model for the agent.")
        logger.debug(f"[CUGA] Requested LLM provider: {self.agent_llm}")

        if not isinstance(self.agent_llm, str):
            logger.debug("[CUGA] Agent LLM is already a model instance.")
            return self.agent_llm, None

        try:
            provider_info = MODEL_PROVIDERS_DICT.get(self.agent_llm)
            if not provider_info:
                msg = f"Invalid model provider: {self.agent_llm}"
                raise ValueError(msg)

            component_class = provider_info.get("component_class")
            display_name = component_class.display_name
            inputs = provider_info.get("inputs")
            prefix = provider_info.get("prefix", "")
            logger.debug(f"[CUGA] Successfully built LLM model from provider: {self.agent_llm}")
            return self._build_llm_model(component_class, inputs, prefix), display_name

        except (AttributeError, ValueError, TypeError, RuntimeError) as e:
            await logger.aerror(f"[CUGA] Error building {self.agent_llm} language model: {e!s}")
            msg = f"Failed to initialize language model: {e!s}"
            raise ValueError(msg) from e

    def _build_llm_model(self, component, inputs, prefix=""):
        """根据组件与输入字段构建 LLM 实例。"""
        model_kwargs = {}
        for input_ in inputs:
            if hasattr(self, f"{prefix}{input_.name}"):
                model_kwargs[input_.name] = getattr(self, f"{prefix}{input_.name}")
        return component.set(**model_kwargs).build_model()

    def set_component_params(self, component):
        """根据提供方配置组件参数并返回组件实例。"""
        provider_info = MODEL_PROVIDERS_DICT.get(self.agent_llm)
        if provider_info:
            inputs = provider_info.get("inputs")
            prefix = provider_info.get("prefix")
            model_kwargs = {}
            for input_ in inputs:
                if hasattr(self, f"{prefix}{input_.name}"):
                    model_kwargs[input_.name] = getattr(self, f"{prefix}{input_.name}")
            return component.set(**model_kwargs)
        return component

    def delete_fields(self, build_config: dotdict, fields: dict | list[str]) -> None:
        """从 build_config 中删除指定字段。"""
        for field in fields:
            build_config.pop(field, None)

    def update_input_types(self, build_config: dotdict) -> dotdict:
        """补齐 build_config 中缺失的 input_types。"""
        for key, value in build_config.items():
            if isinstance(value, dict):
                if value.get("input_types") is None:
                    build_config[key]["input_types"] = []
            elif hasattr(value, "input_types") and value.input_types is None:
                value.input_types = []
        return build_config

    async def update_build_config(
        self, build_config: dotdict, field_value: str, field_name: str | None = None
    ) -> dotdict:
        """按字段变更动态更新构建配置。

        关键路径（三步）：
        1) 根据提供方切换增删字段
        2) 处理动态更新字段的配置刷新
        3) 校验必需字段并返回更新后的配置
        异常流：缺少必需字段时抛 `ValueError`。
        """
        if field_name in ("agent_llm",):
            build_config["agent_llm"]["value"] = field_value
            provider_info = MODEL_PROVIDERS_DICT.get(field_value)
            if provider_info:
                component_class = provider_info.get("component_class")
                if component_class and hasattr(component_class, "update_build_config"):
                    build_config = await update_component_build_config(
                        component_class, build_config, field_value, "model_name"
                    )

            provider_configs: dict[str, tuple[dict, list[dict]]] = {
                provider: (
                    MODEL_PROVIDERS_DICT[provider]["fields"],
                    [
                        MODEL_PROVIDERS_DICT[other_provider]["fields"]
                        for other_provider in MODEL_PROVIDERS_DICT
                        if other_provider != provider
                    ],
                )
                for provider in MODEL_PROVIDERS_DICT
            }
            if field_value in provider_configs:
                fields_to_add, fields_to_delete = provider_configs[field_value]

                # 实现：删除其他提供方字段
                for fields in fields_to_delete:
                    self.delete_fields(build_config, fields)

                # 实现：添加当前提供方字段
                if field_value == "OpenAI" and not any(field in build_config for field in fields_to_add):
                    build_config.update(fields_to_add)
                else:
                    build_config.update(fields_to_add)
                build_config["agent_llm"]["input_types"] = []
            elif field_value == "Custom":
                # 实现：删除所有提供方字段
                self.delete_fields(build_config, ALL_PROVIDER_FIELDS)
                # 实现：替换为自定义模型选择器
                custom_component = DropdownInput(
                    name="agent_llm",
                    display_name="Language Model",
                    options=[*sorted(MODEL_PROVIDERS), "Custom"],
                    value="Custom",
                    real_time_refresh=True,
                    input_types=["LanguageModel"],
                    options_metadata=[MODELS_METADATA[key] for key in sorted(MODELS_METADATA.keys())]
                    + [{"icon": "brain"}],
                )
                build_config.update({"agent_llm": custom_component.to_dict()})

            # 实现：更新 input_types
            build_config = self.update_input_types(build_config)

            # 注意：校验必需字段
            default_keys = [
                "code",
                "_type",
                "agent_llm",
                "tools",
                "input_value",
                "add_current_date_tool",
                "instructions",
                "agent_description",
                "max_iterations",
                "handle_parsing_errors",
                "verbose",
            ]
            missing_keys = [key for key in default_keys if key not in build_config]
            if missing_keys:
                msg = f"Missing required keys in build_config: {missing_keys}"
                raise ValueError(msg)

        if (
            isinstance(self.agent_llm, str)
            and self.agent_llm in MODEL_PROVIDERS_DICT
            and field_name in MODEL_DYNAMIC_UPDATE_FIELDS
        ):
            provider_info = MODEL_PROVIDERS_DICT.get(self.agent_llm)
            if provider_info:
                component_class = provider_info.get("component_class")
                component_class = self.set_component_params(component_class)
                prefix = provider_info.get("prefix")
                if component_class and hasattr(component_class, "update_build_config"):
                    if isinstance(field_name, str) and isinstance(prefix, str):
                        field_name = field_name.replace(prefix, "")
                    build_config = await update_component_build_config(
                        component_class, build_config, field_value, "model_name"
                    )
        return dotdict({k: v.to_dict() if hasattr(v, "to_dict") else v for k, v in build_config.items()})

    async def _get_tools(self) -> list[Tool]:
        """构建 Cuga 代理可用的工具列表。"""
        logger.debug("[CUGA] Building agent tools.")
        component_toolkit = _get_component_toolkit()
        tools_names = self._build_tools_names()
        agent_description = self.get_tool_description()
        description = f"{agent_description}{tools_names}"
        tools = component_toolkit(component=self).get_tools(
            tool_name="Call_CugaAgent", tool_description=description, callbacks=self.get_langchain_callbacks()
        )
        if hasattr(self, "tools_metadata"):
            tools = component_toolkit(component=self, metadata=self.tools_metadata).update_tools_metadata(tools=tools)
        logger.debug(f"[CUGA] Tools built: {[tool.name for tool in tools]}")
        return tools
