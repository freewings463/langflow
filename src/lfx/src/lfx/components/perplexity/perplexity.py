"""
模块名称：Perplexity 文本生成组件

模块目的：提供可在 Langflow 运行时调用的 Perplexity 语言模型组件。
使用场景：在流程中配置 Perplexity LLM 节点并生成文本。
主要功能包括：
- 定义 Perplexity 相关输入参数（模型名、采样参数、输出长度）
- 使用 `ChatPerplexity` 创建模型实例

关键组件：
- `PerplexityComponent`：模型组件入口

设计背景：复用 LangChain 社区实现以降低维护成本。
注意：`api_key` 缺失或无效会导致构建或后续调用失败，调用方需提示配置问题。
"""

from langchain_community.chat_models import ChatPerplexity
from pydantic.v1 import SecretStr

from lfx.base.models.model import LCModelComponent
from lfx.field_typing import LanguageModel
from lfx.field_typing.range_spec import RangeSpec
from lfx.io import DropdownInput, FloatInput, IntInput, SecretStrInput, SliderInput


class PerplexityComponent(LCModelComponent):
    """Perplexity 文本生成组件。

    契约：基于组件输入返回 `LanguageModel`，输入需包含 `api_key` 与 `model_name`。
    关键路径：由 `build_model` 完成参数整理与客户端构建。

    决策：通过 `ChatPerplexity` 适配 LangChain 接口
    问题：需要与 `LCModelComponent` 生态保持一致
    方案：复用 `langchain_community` 的封装能力
    代价：受其参数支持范围与版本稳定性影响
    重评：当上游接口变更或需原生 API 特性时
    """
    display_name = "Perplexity"
    description = "Generate text using Perplexity LLMs."
    documentation = "https://python.langchain.com/v0.2/docs/integrations/chat/perplexity/"
    icon = "Perplexity"
    name = "PerplexityModel"

    inputs = [
        *LCModelComponent.get_base_inputs(),
        DropdownInput(
            name="model_name",
            display_name="Model Name",
            advanced=False,
            options=[
                "llama-3.1-sonar-small-128k-online",
                "llama-3.1-sonar-large-128k-online",
                "llama-3.1-sonar-huge-128k-online",
                "llama-3.1-sonar-small-128k-chat",
                "llama-3.1-sonar-large-128k-chat",
                "llama-3.1-8b-instruct",
                "llama-3.1-70b-instruct",
            ],
            value="llama-3.1-sonar-small-128k-online",
        ),
        IntInput(name="max_tokens", display_name="Max Output Tokens", info="The maximum number of tokens to generate."),
        SecretStrInput(
            name="api_key",
            display_name="Perplexity API Key",
            info="The Perplexity API Key to use for the Perplexity model.",
            advanced=False,
            required=True,
        ),
        SliderInput(
            name="temperature", display_name="Temperature", value=0.75, range_spec=RangeSpec(min=0, max=2, step=0.05)
        ),
        FloatInput(
            name="top_p",
            display_name="Top P",
            info="The maximum cumulative probability of tokens to consider when sampling.",
            advanced=True,
        ),
        IntInput(
            name="n",
            display_name="N",
            info="Number of chat completions to generate for each prompt. "
            "Note that the API may not return the full n completions if duplicates are generated.",
            advanced=True,
        ),
    ]

    def build_model(self) -> LanguageModel:  # type: ignore[type-var]
        """构建可运行的 Perplexity 模型实例。

        契约：读取组件字段并返回 `LanguageModel`。
        副作用：创建外部 API 客户端（网络 I/O 在后续调用阶段发生）。

        关键路径（三步）：
        1) 解密 `api_key` 并整理输入参数
        2) 初始化 `ChatPerplexity`
        3) 返回模型实例供运行时调用

        注意：`api_key` 为空或无效会导致构建或调用阶段抛异常。
        性能：远端推理耗时，吞吐受模型与请求并发影响。
        排障：关注上游异常堆栈与 API 返回错误信息。
        """
        api_key = SecretStr(self.api_key).get_secret_value()
        temperature = self.temperature
        model = self.model_name
        max_tokens = self.max_tokens
        top_p = self.top_p
        n = self.n

        return ChatPerplexity(
            model=model,
            temperature=temperature or 0.75,
            pplx_api_key=api_key,
            top_p=top_p or None,
            n=n or 1,
            max_tokens=max_tokens,
        )
