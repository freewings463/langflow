"""
模块名称：Agent 组件

本模块提供工具调用型 Agent 的组件封装，支持动态工具、记忆读取与结构化输出。
主要功能包括：
- 构建工具调用 Agent 并运行；
- 支持结构化 JSON 输出与 schema 校验；
- 按需注入当前日期工具与共享回调。

关键组件：
- AgentComponent：代理组件入口，面向 Langflow 节点配置与执行。

设计背景：统一代理执行与结构化输出能力，避免不同流程重复拼装。
注意事项：结构化输出依赖模型遵循格式指令，校验失败会回退原始 JSON。
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from pydantic import ValidationError

from lfx.components.models_and_agents.memory import MemoryComponent

if TYPE_CHECKING:
    from langchain_core.tools import Tool

from lfx.base.agents.agent import LCToolsAgentComponent
from lfx.base.agents.events import ExceptionWithMessageError
from lfx.base.models.unified_models import (
    get_language_model_options,
    get_llm,
    update_model_options_in_build_config,
)
from lfx.components.helpers import CurrentDateComponent
from lfx.components.langchain_utilities.tool_calling import ToolCallingAgentComponent
from lfx.custom.custom_component.component import get_component_toolkit
from lfx.helpers.base_model import build_model_from_schema
from lfx.inputs.inputs import BoolInput, ModelInput
from lfx.io import IntInput, MessageTextInput, MultilineInput, Output, SecretStrInput, TableInput
from lfx.log.logger import logger
from lfx.schema.data import Data
from lfx.schema.dotdict import dotdict
from lfx.schema.message import Message
from lfx.schema.table import EditMode


def set_advanced_true(component_input):
    """将输入标记为高级配置，用于隐藏不常用字段。"""
    component_input.advanced = True
    return component_input


class AgentComponent(ToolCallingAgentComponent):
    """工具调用型 Agent 组件

    契约：依赖 `model`、`tools` 与 `system_prompt`；输出 `Message` 或结构化 `Data`。
    关键路径：1) 构建 LLM 与工具集 2) 运行 Agent 3) 处理结构化输出。
    决策：使用 ToolCallingAgent 统一代理执行逻辑
    问题：不同代理模式差异导致行为不一致
    方案：继承 `ToolCallingAgentComponent` 并复用工具调用流程
    代价：对底层代理实现存在耦合
    重评：当需要多代理策略并行或可插拔时
    """
    display_name: str = "Agent"
    description: str = "Define the agent's instructions, then enter a task to complete using tools."
    documentation: str = "https://docs.langflow.org/agents"
    icon = "bot"
    beta = False
    name = "Agent"

    memory_inputs = [set_advanced_true(component_input) for component_input in MemoryComponent().inputs]

    inputs = [
        ModelInput(
            name="model",
            display_name="Language Model",
            info="Select your model provider",
            real_time_refresh=True,
            required=True,
        ),
        SecretStrInput(
            name="api_key",
            display_name="API Key",
            info="Model Provider API key",
            real_time_refresh=True,
            advanced=True,
        ),
        MultilineInput(
            name="system_prompt",
            display_name="Agent Instructions",
            info="System Prompt: Initial instructions and context provided to guide the agent's behavior.",
            value="You are a helpful assistant that can use tools to answer questions and perform tasks.",
            advanced=False,
        ),
        MessageTextInput(
            name="context_id",
            display_name="Context ID",
            info="The context ID of the chat. Adds an extra layer to the local memory.",
            value="",
            advanced=True,
        ),
        IntInput(
            name="n_messages",
            display_name="Number of Chat History Messages",
            value=100,
            info="Number of chat history messages to retrieve.",
            advanced=True,
            show=True,
        ),
        MultilineInput(
            name="format_instructions",
            display_name="Output Format Instructions",
            info="Generic Template for structured output formatting. Valid only with Structured response.",
            value=(
                "You are an AI that extracts structured JSON objects from unstructured text. "
                "Use a predefined schema with expected types (str, int, float, bool, dict). "
                "Extract ALL relevant instances that match the schema - if multiple patterns exist, capture them all. "
                "Fill missing or ambiguous values with defaults: null for missing values. "
                "Remove exact duplicates but keep variations that have different field values. "
                "Always return valid JSON in the expected format, never throw errors. "
                "If multiple objects can be extracted, return them all in the structured format."
            ),
            advanced=True,
        ),
        TableInput(
            name="output_schema",
            display_name="Output Schema",
            info=(
                "Schema Validation: Define the structure and data types for structured output. "
                "No validation if no output schema."
            ),
            advanced=True,
            required=False,
            value=[],
            table_schema=[
                {
                    "name": "name",
                    "display_name": "Name",
                    "type": "str",
                    "description": "Specify the name of the output field.",
                    "default": "field",
                    "edit_mode": EditMode.INLINE,
                },
                {
                    "name": "description",
                    "display_name": "Description",
                    "type": "str",
                    "description": "Describe the purpose of the output field.",
                    "default": "description of field",
                    "edit_mode": EditMode.POPOVER,
                },
                {
                    "name": "type",
                    "display_name": "Type",
                    "type": "str",
                    "edit_mode": EditMode.INLINE,
                    "description": ("Indicate the data type of the output field (e.g., str, int, float, bool, dict)."),
                    "options": ["str", "int", "float", "bool", "dict"],
                    "default": "str",
                },
                {
                    "name": "multiple",
                    "display_name": "As List",
                    "type": "boolean",
                    "description": "Set to True if this output field should be a list of the specified type.",
                    "default": "False",
                    "edit_mode": EditMode.INLINE,
                },
            ],
        ),
        *LCToolsAgentComponent.get_base_inputs(),
        # 注意：当前组件不直接暴露 memory 输入，避免与独立 Memory 组件重复。
        # *memory_inputs,
        BoolInput(
            name="add_current_date_tool",
            display_name="Current Date",
            advanced=True,
            info="If true, will add a tool to the agent that returns the current date.",
            value=True,
        ),
    ]
    outputs = [
        Output(name="response", display_name="Response", method="message_response"),
    ]

    async def get_agent_requirements(self):
        """准备 Agent 运行所需依赖

        契约：返回 `(llm_model, chat_history, tools)`；失败时抛 `ValueError/TypeError`。
        关键路径（三步）：
        1) 构建 LLM 实例并校验
        2) 读取记忆并标准化为列表
        3) 按需注入日期工具并设置回调
        异常流：模型为空或工具类型不符时抛异常，调用方需终止执行。
        排障入口：日志 `Retrieved <n> chat history`。
        决策：在此处统一装配工具与回调
        问题：工具与回调分散装配易遗漏
        方案：集中在 requirements 阶段完成
        代价：方法职责偏重
        重评：当组件拆分为独立装配阶段时
        """
        from langchain_core.tools import StructuredTool

        llm_model = get_llm(
            model=self.model,
            user_id=self.user_id,
            api_key=self.api_key,
        )
        if llm_model is None:
            msg = "No language model selected. Please choose a model to proceed."
            raise ValueError(msg)

        # 实现：读取记忆消息并规整为列表，避免单条消息导致分支复杂化。
        self.chat_history = await self.get_memory_data()
        await logger.adebug(f"Retrieved {len(self.chat_history)} chat history messages")
        if isinstance(self.chat_history, Message):
            self.chat_history = [self.chat_history]

        # 决策：按需注入日期工具，避免默认增加工具数量影响推理。
        if self.add_current_date_tool:
            if not isinstance(self.tools, list):  # type: ignore[has-type]
                self.tools = []
            current_date_tool = (await CurrentDateComponent(**self.get_base_args()).to_toolkit()).pop(0)

            if not isinstance(current_date_tool, StructuredTool):
                msg = "CurrentDateComponent must be converted to a StructuredTool"
                raise TypeError(msg)
            self.tools.append(current_date_tool)

        # 排障：共享回调用于追踪工具调用链路。
        self.set_tools_callbacks(self.tools, self._get_shared_callbacks())

        return llm_model, self.chat_history, self.tools

    async def message_response(self) -> Message:
        """运行 Agent 并返回响应消息

        契约：返回 `Message`；失败抛出异常供上层处理。
        关键路径：1) 获取依赖 2) 构建 agent runnable 3) 执行并返回结果。
        异常流：捕获已知错误并记录日志，未知错误透传。
        排障入口：日志 `Unexpected error` 与异常类型。
        决策：对已知异常类型记录并抛出，避免静默失败
        问题：吞掉异常会导致上层无法感知失败
        方案：记录后重新抛出
        代价：上层需处理更多异常分支
        重评：当统一异常处理中间件落地时
        """
        try:
            llm_model, self.chat_history, self.tools = await self.get_agent_requirements()
            # 实现：将运行参数集中设置，保持 agent runnable 纯配置。
            self.set(
                llm=llm_model,
                tools=self.tools or [],
                chat_history=self.chat_history,
                input_value=self.input_value,
                system_prompt=self.system_prompt,
            )
            agent = self.create_agent_runnable()
            result = await self.run_agent(agent)

            # 注意：缓存结果以支持 JSON 输出模式复用。
            self._agent_result = result

        except (ValueError, TypeError, KeyError) as e:
            await logger.aerror(f"{type(e).__name__}: {e!s}")
            raise
        except ExceptionWithMessageError as e:
            await logger.aerror(f"ExceptionWithMessageError occurred: {e}")
            raise
        # 注意：避免吞掉未知异常，便于上层感知与排障。
        except Exception as e:
            await logger.aerror(f"Unexpected error: {e!s}")
            raise
        else:
            return result

    def _preprocess_schema(self, schema):
        """预处理输出 schema 以保证类型字段一致

        契约：输入 schema 列表，返回标准化字段定义列表。
        关键路径：1) 规范化字段值类型 2) 修正 `multiple` 布尔值。
        决策：在构建模型前标准化 schema
        问题：用户输入可能包含字符串布尔或缺省字段
        方案：统一转为安全类型
        代价：无法保留原始输入格式
        重评：当 schema 输入被强类型约束时
        """
        processed_schema = []
        for field in schema:
            processed_field = {
                "name": str(field.get("name", "field")),
                "type": str(field.get("type", "str")),
                "description": str(field.get("description", "")),
                "multiple": field.get("multiple", False),
            }
            # 注意：`multiple` 允许来自字符串输入，需统一为布尔值。
            if isinstance(processed_field["multiple"], str):
                processed_field["multiple"] = processed_field["multiple"].lower() in [
                    "true",
                    "1",
                    "t",
                    "y",
                    "yes",
                ]
            processed_schema.append(processed_field)
        return processed_schema

    async def build_structured_output_base(self, content: str):
        """构建结构化输出（可选 BaseModel 校验）

        契约：输入 `content` 字符串；返回解析后的 JSON 或包含错误信息的结构。
        关键路径（三步）：
        1) 解析 JSON 或提取 JSON 片段
        2) 无 schema 时直接返回解析结果
        3) 有 schema 时执行 BaseModel 校验
        异常流：解析失败返回 `{"content": ..., "error": ...}`。
        决策：先尝试原始 JSON，再回退正则提取
        问题：模型输出常混有解释文本
        方案：二阶段解析提升成功率
        代价：正则提取可能误匹配大段文本
        重评：当模型输出严格遵循 JSON 时
        """
        json_pattern = r"\{.*\}"
        schema_error_msg = "Try setting an output schema"

        # 实现：先按完整 JSON 解析，失败再做正则提取。
        json_data = None
        try:
            json_data = json.loads(content)
        except json.JSONDecodeError:
            json_match = re.search(json_pattern, content, re.DOTALL)
            if json_match:
                try:
                    json_data = json.loads(json_match.group())
                except json.JSONDecodeError:
                    return {"content": content, "error": schema_error_msg}
            else:
                return {"content": content, "error": schema_error_msg}

        # 注意：未配置 schema 时跳过校验，避免误拒绝有效输出。
        if not hasattr(self, "output_schema") or not self.output_schema or len(self.output_schema) == 0:
            return json_data

        # 实现：使用 BaseModel 进行强校验，失败回传错误明细。
        try:
            processed_schema = self._preprocess_schema(self.output_schema)
            output_model = build_model_from_schema(processed_schema)

            # 实现：按 schema 校验，失败时保留错误信息。
            if isinstance(json_data, list):
                # 注意：多对象输出逐条校验，保留每条错误信息。
                validated_objects = []
                for item in json_data:
                    try:
                        validated_obj = output_model.model_validate(item)
                        validated_objects.append(validated_obj.model_dump())
                    except ValidationError as e:
                        await logger.aerror(f"Validation error for item: {e}")
                        # 注意：保留错误详情，便于定位 schema 不匹配原因。
                        validated_objects.append({"data": item, "validation_error": str(e)})
                return validated_objects

            # 单对象输出保持列表形态便于下游统一处理。
            try:
                validated_obj = output_model.model_validate(json_data)
                # 注意：统一返回列表，避免下游类型分叉。
                return [validated_obj.model_dump()]
            except ValidationError as e:
                await logger.aerror(f"Validation error: {e}")
                return [{"data": json_data, "validation_error": str(e)}]

        except (TypeError, ValueError) as e:
            await logger.aerror(f"Error building structured output: {e}")
            # 排障：校验失败时回退原始解析结果，避免阻断流程。
            return json_data

    async def json_response(self) -> Data:
        """将 Agent 输出转换为结构化 JSON

        契约：返回 `Data`；成功为结构化字段，失败包含 `error`。
        关键路径（三步）：
        1) 拼接系统指令与 schema 说明
        2) 运行结构化 agent 获取内容
        3) 解析与校验结构化输出
        异常流：结构化 agent 失败时返回 `{"content": ..., "error": ...}`。
        排障入口：日志 `Error with structured chat agent`。
        决策：结构化模式独立于常规响应
        问题：普通模式难保证稳定 JSON
        方案：专用指令 + schema 引导
        代价：提示词变长，可能增加 token 开销
        重评：当模型原生结构化输出稳定时
        """
        # 决策：JSON 模式固定走结构化 agent，以提升格式稳定性。
        try:
            system_components = []

            # 实现：先拼接系统指令，再追加格式/Schema 约束。
            agent_instructions = getattr(self, "system_prompt", "") or ""
            if agent_instructions:
                system_components.append(f"{agent_instructions}")

            format_instructions = getattr(self, "format_instructions", "") or ""
            if format_instructions:
                system_components.append(f"Format instructions: {format_instructions}")

            if hasattr(self, "output_schema") and self.output_schema and len(self.output_schema) > 0:
                try:
                    processed_schema = self._preprocess_schema(self.output_schema)
                    output_model = build_model_from_schema(processed_schema)
                    schema_dict = output_model.model_json_schema()
                    schema_info = (
                        "You are given some text that may include format instructions, "
                        "explanations, or other content alongside a JSON schema.\n\n"
                        "Your task:\n"
                        "- Extract only the JSON schema.\n"
                        "- Return it as valid JSON.\n"
                        "- Do not include format instructions, explanations, or extra text.\n\n"
                        "Input:\n"
                        f"{json.dumps(schema_dict, indent=2)}\n\n"
                        "Output (only JSON schema):"
                    )
                    system_components.append(schema_info)
                except (ValidationError, ValueError, TypeError, KeyError) as e:
                    await logger.aerror(f"Could not build schema for prompt: {e}", exc_info=True)

            # 注意：仅当存在组件时拼接，避免空提示干扰模型。
            combined_instructions = "\n\n".join(system_components) if system_components else ""
            llm_model, self.chat_history, self.tools = await self.get_agent_requirements()
            self.set(
                llm=llm_model,
                tools=self.tools or [],
                chat_history=self.chat_history,
                input_value=self.input_value,
                system_prompt=combined_instructions,
            )

            try:
                structured_agent = self.create_agent_runnable()
            except (NotImplementedError, ValueError, TypeError) as e:
                await logger.aerror(f"Error with structured chat agent: {e}")
                raise
            try:
                result = await self.run_agent(structured_agent)
            except (
                ExceptionWithMessageError,
                ValueError,
                TypeError,
                RuntimeError,
            ) as e:
                await logger.aerror(f"Error with structured agent result: {e}")
                raise
            # 实现：兼容多种返回体字段。
            if hasattr(result, "content"):
                content = result.content
            elif hasattr(result, "text"):
                content = result.text
            else:
                content = str(result)

        except (
            ExceptionWithMessageError,
            ValueError,
            TypeError,
            NotImplementedError,
            AttributeError,
        ) as e:
            await logger.aerror(f"Error with structured chat agent: {e}")
            # 排障：结构化模式失败时回退，并携带错误信息。
            content_str = "No content returned from agent"
            return Data(data={"content": content_str, "error": str(e)})

        # 实现：统一入口处理结构化校验与输出封装。
        try:
            structured_output = await self.build_structured_output_base(content)

            # 注意：不同格式统一包装为 Data，避免前端解析分叉。
            if isinstance(structured_output, list) and structured_output:
                if len(structured_output) == 1:
                    return Data(data=structured_output[0])
                return Data(data={"results": structured_output})
            if isinstance(structured_output, dict):
                return Data(data=structured_output)
            return Data(data={"content": content})

        except (ValueError, TypeError) as e:
            await logger.aerror(f"Error in structured output processing: {e}")
            return Data(data={"content": content, "error": str(e)})

    async def get_memory_data(self):
        """获取去重后的历史消息

        契约：返回消息列表；过滤与当前输入重复的消息。
        关键路径：1) 调用 MemoryComponent 检索 2) 过滤重复消息 3) 返回列表。
        异常流：MemoryComponent 失败将向上抛出异常。
        决策：在组件层做去重而非数据库层
        问题：同一消息可能被重复取回
        方案：基于 message.id 过滤当前输入
        代价：仅能过滤有 id 的消息
        重评：当存储层提供去重查询时
        """
        # 注意：这是临时去重逻辑，后续应下沉到统一检索函数。
        messages = (
            await MemoryComponent(**self.get_base_args())
            .set(
                session_id=self.graph.session_id,
                context_id=self.context_id,
                order="Ascending",
                n_messages=self.n_messages,
            )
            .retrieve_messages()
        )
        return [
            message for message in messages if getattr(message, "id", None) != getattr(self.input_value, "id", None)
        ]

    def update_input_types(self, build_config: dotdict) -> dotdict:
        """补全输入类型列表

        契约：为缺失 `input_types` 的字段填充空列表，返回更新后的配置。
        关键路径：1) 遍历配置 2) 补齐字段默认值。
        决策：在组件层做兜底填充
        问题：部分字段未定义 input_types 会影响前端渲染
        方案：统一补齐为空列表
        代价：可能掩盖上游配置缺失
        重评：当上游保证强约束时
        """
        for key, value in build_config.items():
            if isinstance(value, dict):
                if value.get("input_types") is None:
                    build_config[key]["input_types"] = []
            elif hasattr(value, "input_types") and value.input_types is None:
                value.input_types = []
        return build_config

    async def update_build_config(
        self,
        build_config: dotdict,
        field_value: list[dict],
        field_name: str | None = None,
    ) -> dotdict:
        """更新构建配置并校验必填字段

        契约：返回更新后的 `dotdict`；模型选项使用缓存更新。
        关键路径（三步）：
        1) 更新可用模型列表（仅支持工具调用）
        2) 补齐输入类型列表
        3) 校验必填键并返回
        异常流：缺失必填键时抛 `ValueError`。
        决策：限定仅可工具调用的模型
        问题：Agent 依赖工具调用能力
        方案：调用 `get_language_model_options(..., tool_calling=True)`
        代价：过滤掉不支持工具调用的模型
        重评：当代理支持非工具调用模式时
        """
        # 注意：Agent 必须支持工具调用，因此只加载可用模型。
        def get_tool_calling_model_options(user_id=None):
            return get_language_model_options(user_id=user_id, tool_calling=True)

        build_config = update_model_options_in_build_config(
            component=self,
            build_config=dict(build_config),
            cache_key_prefix="language_model_options_tool_calling",
            get_options_func=get_tool_calling_model_options,
            field_name=field_name,
            field_value=field_value,
        )
        build_config = dotdict(build_config)

        if field_name == "model":
            self.log(str(field_value))
            # 实现：补齐输入类型，避免前端依赖字段缺失。
            build_config = self.update_input_types(build_config)

            # 注意：必填键缺失会导致前端/运行时崩溃。
            default_keys = [
                "code",
                "_type",
                "model",
                "tools",
                "input_value",
                "add_current_date_tool",
                "system_prompt",
                "agent_description",
                "max_iterations",
                "handle_parsing_errors",
                "verbose",
            ]
            missing_keys = [key for key in default_keys if key not in build_config]
            if missing_keys:
                msg = f"Missing required keys in build_config: {missing_keys}"
                raise ValueError(msg)
        return dotdict({k: v.to_dict() if hasattr(v, "to_dict") else v for k, v in build_config.items()})

    async def _get_tools(self) -> list[Tool]:
        """构建可暴露给外部的 Agent 工具

        契约：返回 `Tool` 列表；使用组件工具包生成并附带回调。
        关键路径：1) 组装工具描述 2) 调用工具包生成工具 3) 写入元数据。
        异常流：工具包内部异常将向上抛出。
        """
        component_toolkit = get_component_toolkit()
        tools_names = self._build_tools_names()
        agent_description = self.get_tool_description()
        # 注意：Agent Description 属于历史兼容字段，后续计划移除。
        description = f"{agent_description}{tools_names}"

        tools = component_toolkit(component=self).get_tools(
            tool_name="Call_Agent",
            tool_description=description,
            # 注意：作为工具暴露时不复用共享回调，避免循环回调链路。
            callbacks=self.get_langchain_callbacks(),
        )
        if hasattr(self, "tools_metadata"):
            tools = component_toolkit(component=self, metadata=self.tools_metadata).update_tools_metadata(tools=tools)

        return tools
