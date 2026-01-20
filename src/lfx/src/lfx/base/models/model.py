"""
模块名称：模型组件基类

本模块提供 LFX 的语言模型组件基类，实现统一的输入处理、消息构建、流式输出与
错误处理逻辑。
主要功能包括：
- 构建模型实例并提供标准输出
- 统一处理系统消息、流式/非流式调用
- 生成状态信息与异常提示

关键组件：
- `LCModelComponent`：模型组件基类

设计背景：统一模型调用与 UI 输出协议，降低各提供方差异带来的重复实现。
注意事项：部分逻辑依赖 LangChain 行为与输出结构，升级需同步验证。
"""

import importlib
import json
import warnings
from abc import abstractmethod

from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.llms import LLM
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.output_parsers import BaseOutputParser

from lfx.base.constants import STREAM_INFO_TEXT
from lfx.custom.custom_component.component import Component
from lfx.field_typing import LanguageModel
from lfx.inputs.inputs import BoolInput, InputTypes, MessageInput, MultilineInput
from lfx.schema.message import Message
from lfx.template.field.base import Output
from lfx.utils.constants import MESSAGE_SENDER_AI

# NVIDIA 推理模型使用的详细思考前缀。
#
# 注意：模型训练时固定使用该字符串，请勿修改。
DETAILED_THINKING_PREFIX = "detailed thinking on\n\n"


class LCModelComponent(Component):
    """语言模型组件基类。

    契约：`build_model()` 返回 LangChain 兼容的 `LanguageModel`；
    `text_response()` 输出 `Message`。
    副作用：可能触发远程模型调用并更新 `self.status`。
    失败语义：模型调用失败会抛 `ValueError`（若可提取异常信息）。
    决策：在基类统一处理消息构建与流式输出
    问题：各模型组件重复实现消息/流式逻辑
    方案：在基类集中处理，子类仅负责构建模型
    代价：基类逻辑较复杂，需与 LangChain 行为保持同步
    重评：当组件体系拆分为独立运行时再评估下沉
    """

    display_name: str = "Model Name"
    description: str = "Model Description"
    trace_type = "llm"
    metadata = {
        "keywords": [
            "model",
            "llm",
            "language model",
            "large language model",
        ],
    }

    # 可选输出解析器：子类可允许用户输入 `output_parser`
    output_parser: BaseOutputParser | None = None

    _base_inputs: list[InputTypes] = [
        MessageInput(name="input_value", display_name="Input"),
        MultilineInput(
            name="system_message",
            display_name="System Message",
            info="System message to pass to the model.",
            advanced=False,
        ),
        BoolInput(name="stream", display_name="Stream", info=STREAM_INFO_TEXT, advanced=True),
    ]

    outputs = [
        Output(display_name="Model Response", name="text_output", method="text_response"),
        Output(display_name="Language Model", name="model_output", method="build_model"),
    ]

    def _get_exception_message(self, e: Exception):
        """提取异常的展示信息。

        契约：返回可读字符串，供上层包装为 `ValueError`。
        """
        return str(e)

    def supports_tool_calling(self, model: LanguageModel) -> bool:
        """检测模型是否支持工具调用。

        契约：返回布尔值，`True` 表示可绑定工具且工具列表非空。
        失败语义：遇到属性/类型异常返回 `False`。
        """
        try:
            # 若 bind_tools 仍为基类实现，视为不支持
            if model.bind_tools is BaseChatModel.bind_tools:
                return False

            def test_tool(x: int) -> int:
                return x

            model_with_tool = model.bind_tools([test_tool])
            return hasattr(model_with_tool, "tools") and len(model_with_tool.tools) > 0
        except (AttributeError, TypeError, ValueError):
            return False

    def _validate_outputs(self) -> None:
        """校验组件输出声明完整性。"""
        # 至少需要定义以下输出
        required_output_methods = ["text_response", "build_model"]
        output_names = [output.name for output in self.outputs]
        for method_name in required_output_methods:
            if method_name not in output_names:
                msg = f"Output with name '{method_name}' must be defined."
                raise ValueError(msg)
            if not hasattr(self, method_name):
                msg = f"Method '{method_name}' must be defined."
                raise ValueError(msg)

    async def text_response(self) -> Message:
        """异步获取模型响应并更新状态。"""
        output = self.build_model()
        result = await self.get_chat_result(
            runnable=output, stream=self.stream, input_value=self.input_value, system_message=self.system_message
        )
        self.status = result
        return result

    def get_result(self, *, runnable: LLM, stream: bool, input_value: str):
        """从 LLM runnable 获取结果（同步）。

        契约：当 `stream=True` 返回流式结果；否则返回模型输出内容。
        失败语义：调用异常会被包装为 `ValueError`（若可提取消息）。
        """
        try:
            if stream:
                result = runnable.stream(input_value)
            else:
                message = runnable.invoke(input_value)
                result = message.content if hasattr(message, "content") else message
                self.status = result
        except Exception as e:
            if message := self._get_exception_message(e):
                raise ValueError(message) from e
            raise

        return result

    def build_status_message(self, message: AIMessage):
        """根据模型响应元数据构建状态信息。

        契约：返回字符串或包含 token 统计的字典结构。
        失败语义：缺少预期元数据时回退为纯文本响应。
        """
        if message.response_metadata:
            # 依据元数据构建结构化状态信息
            content = message.content
            response_metadata = message.response_metadata
            openai_keys = ["token_usage", "model_name", "finish_reason"]
            inner_openai_keys = ["completion_tokens", "prompt_tokens", "total_tokens"]
            anthropic_keys = ["model", "usage", "stop_reason"]
            inner_anthropic_keys = ["input_tokens", "output_tokens"]
            if all(key in response_metadata for key in openai_keys) and all(
                key in response_metadata["token_usage"] for key in inner_openai_keys
            ):
                token_usage = response_metadata["token_usage"]
                status_message = {
                    "tokens": {
                        "input": token_usage["prompt_tokens"],
                        "output": token_usage["completion_tokens"],
                        "total": token_usage["total_tokens"],
                        "stop_reason": response_metadata["finish_reason"],
                        "response": content,
                    }
                }

            elif all(key in response_metadata for key in anthropic_keys) and all(
                key in response_metadata["usage"] for key in inner_anthropic_keys
            ):
                usage = response_metadata["usage"]
                status_message = {
                    "tokens": {
                        "input": usage["input_tokens"],
                        "output": usage["output_tokens"],
                        "stop_reason": response_metadata["stop_reason"],
                        "response": content,
                    }
                }
            else:
                status_message = f"Response: {content}"  # type: ignore[assignment]
        else:
            status_message = f"Response: {message.content}"  # type: ignore[assignment]
        return status_message

    async def get_chat_result(
        self,
        *,
        runnable: LanguageModel,
        stream: bool,
        input_value: str | Message,
        system_message: str | None = None,
    ) -> Message:
        """根据配置生成聊天结果（含 NVIDIA 推理前缀）。"""
        # NVIDIA 推理模型使用详细思考前缀
        if getattr(self, "detailed_thinking", False):
            system_message = DETAILED_THINKING_PREFIX + (system_message or "")

        return await self._get_chat_result(
            runnable=runnable,
            stream=stream,
            input_value=input_value,
            system_message=system_message,
        )

    async def _get_chat_result(
        self,
        *,
        runnable: LanguageModel,
        stream: bool,
        input_value: str | Message,
        system_message: str | None = None,
    ) -> Message:
        """执行模型调用并返回 `Message` 结果。

        关键路径（三步）：
        1) 将输入转换为 LangChain 消息列表（含可选系统消息）
        2) 注入 output_parser 与运行配置并发起调用
        3) 解析结果并更新 `self.status`
        异常流：空输入抛 `ValueError`；模型调用异常被包装为 `ValueError`。
        性能瓶颈：模型调用网络延迟与流式输出。
        排障入口：检查 `self.status` 与 `response_metadata` 内容。
        """
        messages: list[BaseMessage] = []
        if not input_value and not system_message:
            msg = "The message you want to send to the model is empty."
            raise ValueError(msg)
        system_message_added = False
        message = None
        if input_value:
            if isinstance(input_value, Message):
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    if "prompt" in input_value:
                        prompt = input_value.load_lc_prompt()
                        if system_message:
                            prompt.messages = [
                                SystemMessage(content=system_message),
                                *prompt.messages,  # type: ignore[has-type]
                            ]
                            system_message_added = True
                        runnable = prompt | runnable
                    else:
                        messages.append(input_value.to_lc_message(self.name))
            else:
                messages.append(HumanMessage(content=input_value))

        if system_message and not system_message_added:
            messages.insert(0, SystemMessage(content=system_message))
        inputs: list | dict = messages or {}
        lf_message = None
        try:
            # TODO: 弃用功能，后续版本将移除
            if hasattr(self, "output_parser") and self.output_parser is not None:
                runnable |= self.output_parser

            runnable = runnable.with_config(
                {
                    "run_name": self.display_name,
                    "project_name": self.get_project_name(),
                    "callbacks": self.get_langchain_callbacks(),
                }
            )
            if stream:
                lf_message, result = await self._handle_stream(runnable, inputs)
            else:
                message = await runnable.ainvoke(inputs)
                result = message.content if hasattr(message, "content") else message
            if isinstance(message, AIMessage):
                status_message = self.build_status_message(message)
                self.status = status_message
            elif isinstance(result, dict):
                result = json.dumps(message, indent=4)
                self.status = result
            else:
                self.status = result
        except Exception as e:
            if message := self._get_exception_message(e):
                raise ValueError(message) from e
            raise
        return lf_message or Message(text=result)

    async def _handle_stream(self, runnable, inputs):
        """处理流式响应并在需要时写入消息流。

        契约：返回 `(Message | None, result)`，当连接到聊天输出时返回消息对象。
        失败语义：异常由上层捕获。
        """
        lf_message = None
        if self.is_connected_to_chat_output():
            # 生成并发送流式消息
            if hasattr(self, "graph"):
                session_id = self.graph.session_id
            elif hasattr(self, "_session_id"):
                session_id = self._session_id
            else:
                session_id = None
            model_message = Message(
                text=runnable.astream(inputs),
                sender=MESSAGE_SENDER_AI,
                sender_name="AI",
                properties={"icon": self.icon, "state": "partial"},
                session_id=session_id,
            )
            model_message.properties.source = self._build_source(self._id, self.display_name, self)
            lf_message = await self.send_message(model_message)
            result = lf_message.text or ""
        else:
            message = await runnable.ainvoke(inputs)
            result = message.content if hasattr(message, "content") else message
        return lf_message, result

    @abstractmethod
    def build_model(self) -> LanguageModel:  # type: ignore[type-var]
        """构建并返回具体模型实例。"""

    def get_llm(self, provider_name: str, model_info: dict[str, dict[str, str | list[InputTypes]]]) -> LanguageModel:
        """根据提供方名称与模型信息构建 LLM。

        契约：返回可调用的 `LanguageModel`；未知提供方抛 `ValueError`。
        失败语义：构建失败统一抛 `ValueError` 并包含提供方信息。
        """
        try:
            if provider_name not in [model.get("display_name") for model in model_info.values()]:
                msg = f"Unknown model provider: {provider_name}"
                raise ValueError(msg)

            # 单次遍历获取组件信息与模块名
            component_info, module_name = next(
                ((info, key) for key, info in model_info.items() if info.get("display_name") == provider_name),
                (None, None),
            )
            if not component_info:
                msg = f"Component information not found for {provider_name}"
                raise ValueError(msg)
            component_inputs = component_info.get("inputs", [])
            # 从 models 模块获取组件类
            # 确保 inputs 为列表
            if not isinstance(component_inputs, list):
                component_inputs = []

            import warnings

            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore", message="Support for class-based `config` is deprecated", category=DeprecationWarning
                )
                warnings.filterwarnings("ignore", message="Valid config keys have changed in V2", category=UserWarning)
                models_module = importlib.import_module("lfx.components.models")
                component_class = getattr(models_module, str(module_name))
                component = component_class()

            return self.build_llm_model_from_inputs(component, component_inputs)
        except Exception as e:
            msg = f"Error building {provider_name} language model"
            raise ValueError(msg) from e

    def build_llm_model_from_inputs(
        self, component: Component, inputs: list[InputTypes], prefix: str = ""
    ) -> LanguageModel:
        """根据组件与输入字段构建 LLM。

        契约：按 `inputs` 读取当前组件字段并调用 `component.set(...).build_model()`。
        失败语义：字段缺失会导致模型构建失败并抛异常。
        """
        # 确保 prefix 为字符串
        prefix = prefix or ""
        # 仅采集组件声明的输入字段
        input_data = {
            str(component_input.name): getattr(self, f"{prefix}{component_input.name}", None)
            for component_input in inputs
        }

        return component.set(**input_data).build_model()
