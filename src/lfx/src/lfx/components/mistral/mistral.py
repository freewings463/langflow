"""
模块名称：Mistral 文本生成组件

模块目的：提供可在 Langflow 运行时调用的 Mistral 语言模型组件。
使用场景：在流程中配置 Mistral LLM 节点并生成文本。
主要功能包括：
- 定义 Mistral 相关输入参数（模型名、超时、并发等）
- 使用 `ChatMistralAI` 创建模型实例

关键组件：
- `MistralAIModelComponent`：模型组件入口

设计背景：统一对接 LangChain 生态以复用通用模型接口。
注意：模型构建失败会抛 `ValueError`，调用方需提示配置/网络问题。
"""

from langchain_mistralai import ChatMistralAI
from pydantic.v1 import SecretStr

from lfx.base.models.model import LCModelComponent
from lfx.field_typing import LanguageModel
from lfx.io import BoolInput, DropdownInput, FloatInput, IntInput, SecretStrInput, StrInput


class MistralAIModelComponent(LCModelComponent):
    """Mistral 文本生成组件。

    契约：基于组件输入返回 `LanguageModel`，输入需包含 `api_key`。
    关键路径：由 `build_model` 完成参数整理与客户端构建。

    决策：通过 `ChatMistralAI` 适配 LangChain 接口
    问题：需要与 `LCModelComponent` 生态保持一致
    方案：复用 `langchain_mistralai` 的封装能力
    代价：受其版本与参数支持范围限制
    重评：当上游 SDK 破坏性变更或需要直连 API 时
    """
    display_name = "MistralAI"
    description = "Generates text using MistralAI LLMs."
    icon = "MistralAI"
    name = "MistralModel"

    inputs = [
        *LCModelComponent.get_base_inputs(),
        IntInput(
            name="max_tokens",
            display_name="Max Tokens",
            advanced=True,
            info="The maximum number of tokens to generate. Set to 0 for unlimited tokens.",
        ),
        DropdownInput(
            name="model_name",
            display_name="Model Name",
            advanced=False,
            options=[
                "open-mixtral-8x7b",
                "open-mixtral-8x22b",
                "mistral-small-latest",
                "mistral-medium-latest",
                "mistral-large-latest",
                "codestral-latest",
            ],
            value="codestral-latest",
        ),
        StrInput(
            name="mistral_api_base",
            display_name="Mistral API Base",
            advanced=True,
            info="The base URL of the Mistral API. Defaults to https://api.mistral.ai/v1. "
            "You can change this to use other APIs like JinaChat, LocalAI and Prem.",
        ),
        SecretStrInput(
            name="api_key",
            display_name="Mistral API Key",
            info="The Mistral API Key to use for the Mistral model.",
            advanced=False,
            required=True,
            value="MISTRAL_API_KEY",
        ),
        FloatInput(
            name="temperature",
            display_name="Temperature",
            value=0.1,
            advanced=True,
        ),
        IntInput(
            name="max_retries",
            display_name="Max Retries",
            advanced=True,
            value=5,
        ),
        IntInput(
            name="timeout",
            display_name="Timeout",
            advanced=True,
            value=60,
        ),
        IntInput(
            name="max_concurrent_requests",
            display_name="Max Concurrent Requests",
            advanced=True,
            value=3,
        ),
        FloatInput(
            name="top_p",
            display_name="Top P",
            advanced=True,
            value=1,
        ),
        IntInput(
            name="random_seed",
            display_name="Random Seed",
            value=1,
            advanced=True,
        ),
        BoolInput(
            name="safe_mode",
            display_name="Safe Mode",
            advanced=True,
            value=False,
        ),
    ]

    def build_model(self) -> LanguageModel:  # type: ignore[type-var]
        """将组件输入转换为可运行的 Mistral 模型实例。

        契约：读取组件字段并返回 `LanguageModel`，依赖 `api_key`/`model_name` 等配置。
        副作用：创建外部 API 客户端（网络 I/O 在后续调用阶段发生）。

        关键路径（三步）：
        1) 解密 `api_key` 并整理输入参数
        2) 初始化 `ChatMistralAI`
        3) 返回模型实例供运行时调用

        注意：异常流为初始化异常统一抛 `ValueError`，调用方提示配置/网络问题后重试。
        性能：远端推理耗时，吞吐受 `max_concurrent_requests` 限制。
        排障：异常消息 `Could not connect to MistralAI API.`
        """
        try:
            return ChatMistralAI(
                model_name=self.model_name,
                mistral_api_key=SecretStr(self.api_key).get_secret_value() if self.api_key else None,
                endpoint=self.mistral_api_base or "https://api.mistral.ai/v1",
                max_tokens=self.max_tokens or None,
                temperature=self.temperature,
                max_retries=self.max_retries,
                timeout=self.timeout,
                max_concurrent_requests=self.max_concurrent_requests,
                top_p=self.top_p,
                random_seed=self.random_seed,
                safe_mode=self.safe_mode,
                streaming=self.stream,
            )
        except Exception as e:
            msg = "Could not connect to MistralAI API."
            raise ValueError(msg) from e
