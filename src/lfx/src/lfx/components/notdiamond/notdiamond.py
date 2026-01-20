"""
模块名称：Not Diamond 路由组件

本模块提供对 Not Diamond 模型路由服务的封装，主要用于在多个语言模型之间选择最合适的候选。主要功能包括：
- 组装路由请求（消息、模型候选、权衡策略）
- 调用 Not Diamond API 获取推荐模型
- 回退到默认模型并执行实际推理

关键组件：
- `ND_MODEL_MAPPING`：内部模型名到 Not Diamond provider/model 的映射
- `NotDiamondComponent`：组件主体
- `model_select`：路由选择与调用入口

设计背景：在成本、延迟与质量之间动态选择模型，减少人工配置。
使用场景：同时接入多个模型并希望自动路由时。
注意事项：网络失败会抛异常；API 未返回 provider 时回退第一个模型；请求超时为 10 秒。
"""

import warnings

import requests
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic.v1 import SecretStr

from lfx.base.models.chat_result import get_chat_result
from lfx.base.models.model_utils import get_model_name
from lfx.custom.custom_component.component import Component
from lfx.io import (
    BoolInput,
    DropdownInput,
    HandleInput,
    MessageInput,
    MessageTextInput,
    Output,
    SecretStrInput,
    StrInput,
)
from lfx.schema.message import Message

# 注意：映射需与 Not Diamond 支持的 `provider/model` 保持一致，否则会导致路由不可用。
ND_MODEL_MAPPING = {
    "gpt-4o": {"provider": "openai", "model": "gpt-4o"},
    "gpt-4o-mini": {"provider": "openai", "model": "gpt-4o-mini"},
    "gpt-4-turbo": {"provider": "openai", "model": "gpt-4-turbo-2024-04-09"},
    "claude-3-5-haiku-20241022": {"provider": "anthropic", "model": "claude-3-5-haiku-20241022"},
    "claude-3-5-sonnet-20241022": {"provider": "anthropic", "model": "claude-3-5-sonnet-20241022"},
    "anthropic.claude-3-5-sonnet-20241022-v2:0": {"provider": "anthropic", "model": "claude-3-5-sonnet-20241022"},
    "anthropic.claude-3-5-haiku-20241022-v1:0": {"provider": "anthropic", "model": "claude-3-5-haiku-20241022"},
    "gemini-1.5-pro": {"provider": "google", "model": "gemini-1.5-pro-latest"},
    "gemini-1.5-flash": {"provider": "google", "model": "gemini-1.5-flash-latest"},
    "llama-3.1-sonar-large-128k-online": {"provider": "perplexity", "model": "llama-3.1-sonar-large-128k-online"},
    "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo": {
        "provider": "togetherai",
        "model": "Meta-Llama-3.1-70B-Instruct-Turbo",
    },
    "meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo": {
        "provider": "togetherai",
        "model": "Meta-Llama-3.1-405B-Instruct-Turbo",
    },
    "mistral-large-latest": {"provider": "mistral", "model": "mistral-large-2407"},
}


class NotDiamondComponent(Component):
    """Not Diamond 路由组件。

    契约：输入 `input_value`/`system_message`/`models`/`tradeoff` 等；输出 `model_select` 的 `Message`。
    副作用：调用外部 Not Diamond API（网络 I/O），并更新 `_selected_model_name`。
    失败语义：请求失败/JSON 解析失败将抛异常；API 未返回 provider 时回退到首个模型。
    关键路径：1) 构建 OpenAI 格式消息与候选模型 2) 调用路由 API 3) 选定模型并执行推理。
    """
    display_name = "Not Diamond Router"
    description = "Call the right model at the right time with the world's most powerful AI model router."
    documentation: str = "https://docs.notdiamond.ai/"
    icon = "NotDiamond"
    name = "NotDiamond"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._selected_model_name = None

    inputs = [
        MessageInput(name="input_value", display_name="Input", required=True),
        MessageTextInput(
            name="system_message",
            display_name="System Message",
            info="System message to pass to the model.",
            advanced=False,
        ),
        HandleInput(
            name="models",
            display_name="Language Models",
            input_types=["LanguageModel"],
            required=True,
            is_list=True,
            info="Link the models you want to route between.",
        ),
        SecretStrInput(
            name="api_key",
            display_name="Not Diamond API Key",
            info="The Not Diamond API Key to use for routing.",
            advanced=False,
            value="NOTDIAMOND_API_KEY",
            required=True,
        ),
        StrInput(
            name="preference_id",
            display_name="Preference ID",
            info="The ID of the router preference that was configured via the Dashboard.",
            advanced=False,
        ),
        DropdownInput(
            name="tradeoff",
            display_name="Tradeoff",
            info="The tradeoff between cost and latency for the router to determine the best LLM for a given query.",
            advanced=False,
            options=["quality", "cost", "latency"],
            value="quality",
        ),
        BoolInput(
            name="hash_content",
            display_name="Hash Content",
            info="Whether to hash the content before being sent to the NotDiamond API.",
            advanced=False,
            value=False,
        ),
    ]

    outputs = [
        Output(display_name="Output", name="output", method="model_select"),
        Output(
            display_name="Selected Model",
            name="selected_model",
            method="get_selected_model",
            required_inputs=["output"],
        ),
    ]

    def get_selected_model(self) -> str:
        """返回最近一次路由选择的模型名（可能为空）。"""
        return self._selected_model_name

    def model_select(self) -> Message:
        """调用 Not Diamond API 选择模型并执行推理。

        契约：候选模型来自 `models` 输入；若路由失败则回退到第一个模型。
        副作用：发起 HTTP 请求（10 秒超时）并更新 `_selected_model_name`。
        关键路径（三步）：1) 组装消息与候选模型 payload 2) 调用路由 API 3) 匹配结果并执行推理。
        异常流：网络错误、非 JSON 响应或请求超时直接抛出异常。
        排障入口：检查 Not Diamond API 响应与 `providers` 字段；无 provider 时走回退路径。
        """
        api_key = SecretStr(self.api_key).get_secret_value() if self.api_key else None
        input_value = self.input_value
        system_message = self.system_message
        messages = self._format_input(input_value, system_message)

        selected_models = []
        mapped_selected_models = []
        for model in self.models:
            model_name = get_model_name(model)

            if model_name in ND_MODEL_MAPPING:
                selected_models.append(model)
                mapped_selected_models.append(ND_MODEL_MAPPING[model_name])

        payload = {
            "messages": messages,
            "llm_providers": mapped_selected_models,
            "hash_content": self.hash_content,
        }

        if self.tradeoff != "quality":
            payload["tradeoff"] = self.tradeoff

        if self.preference_id and self.preference_id != "":
            payload["preference_id"] = self.preference_id

        header = {
            "Authorization": f"Bearer {api_key}",
            "accept": "application/json",
            "content-type": "application/json",
        }

        response = requests.post(
            "https://api.notdiamond.ai/v2/modelRouter/modelSelect",
            json=payload,
            headers=header,
            timeout=10,
        )

        result = response.json()
        # 注意：路由失败时回退到首个模型，保证流程可继续执行。
        chosen_model = self.models[0]
        self._selected_model_name = get_model_name(chosen_model)

        if "providers" not in result:
            # 排障：API 未返回 provider，通常代表路由失败，回退首个模型。
            return self._call_get_chat_result(chosen_model, input_value, system_message)

        providers = result["providers"]

        if len(providers) == 0:
            # 排障：provider 为空时直接回退首个模型。
            return self._call_get_chat_result(chosen_model, input_value, system_message)

        nd_result = providers[0]

        for nd_model, selected_model in zip(mapped_selected_models, selected_models, strict=False):
            if nd_model["provider"] == nd_result["provider"] and nd_model["model"] == nd_result["model"]:
                chosen_model = selected_model
                self._selected_model_name = get_model_name(chosen_model)
                break

        return self._call_get_chat_result(chosen_model, input_value, system_message)

    def _call_get_chat_result(self, chosen_model, input_value, system_message):
        """执行模型推理并返回 `Message`。

        契约：`chosen_model` 必须是可运行的 LanguageModel；输入与系统消息透传给下游模型。
        失败语义：下游模型异常原样上抛。
        """
        return get_chat_result(
            runnable=chosen_model,
            input_value=input_value,
            system_message=system_message,
        )

    def _format_input(
        self,
        input_value: str | Message,
        system_message: str | None = None,
    ):
        """将输入格式化为 Not Diamond API 所需的 OpenAI 消息结构。

        契约：支持 `Message` 或纯文本；当两者均为空时抛 `ValueError`。
        副作用：无；失败语义：`Message` 解析失败时会抛出相关异常。
        关键路径：1) 解析 `Message`/文本 2) 合并系统消息 3) 映射为 OpenAI 角色结构。
        """
        messages: list[BaseMessage] = []
        if not input_value and not system_message:
            msg = "The message you want to send to the router is empty."
            raise ValueError(msg)
        system_message_added = False
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
                        messages.extend(prompt.messages)
                    else:
                        messages.append(input_value.to_lc_message())
            else:
                messages.append(HumanMessage(content=input_value))

        if system_message and not system_message_added:
            messages.insert(0, SystemMessage(content=system_message))

        # 实现：Not Diamond 接口使用 OpenAI 风格角色字段，需要做消息格式转换。
        openai_messages = []
        for msg in messages:
            if isinstance(msg, HumanMessage):
                openai_messages.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AIMessage):
                openai_messages.append({"role": "assistant", "content": msg.content})
            elif isinstance(msg, SystemMessage):
                openai_messages.append({"role": "system", "content": msg.content})

        return openai_messages
