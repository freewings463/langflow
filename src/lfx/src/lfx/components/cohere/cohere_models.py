"""
模块名称：cohere_models

本模块提供 Cohere 语言模型组件封装。
主要功能包括：
- 构建并返回 Cohere Chat 模型实例
- 暴露温度等基础生成参数

关键组件：
- `CohereComponent`：Cohere Chat 组件

设计背景：需要在 Langflow 中接入 Cohere 语言模型
使用场景：对话生成、文本生成
注意事项：API Key 必须有效
"""

from langchain_cohere import ChatCohere
from pydantic.v1 import SecretStr

from lfx.base.models.model import LCModelComponent
from lfx.field_typing import LanguageModel
from lfx.field_typing.range_spec import RangeSpec
from lfx.io import SecretStrInput, SliderInput


class CohereComponent(LCModelComponent):
    """Cohere 语言模型组件。

    契约：需提供 `cohere_api_key`；返回实现 `LanguageModel` 的实例。
    副作用：创建 LangChain `ChatCohere` 客户端。
    失败语义：API Key 无效时由下游 SDK 抛错。
    """
    display_name = "Cohere Language Models"
    description = "Generate text using Cohere LLMs."
    documentation = "https://python.langchain.com/docs/integrations/llms/cohere/"
    icon = "Cohere"
    name = "CohereModel"

    inputs = [
        *LCModelComponent.get_base_inputs(),
        SecretStrInput(
            name="cohere_api_key",
            display_name="Cohere API Key",
            info="The Cohere API Key to use for the Cohere model.",
            advanced=False,
            value="COHERE_API_KEY",
            required=True,
        ),
        SliderInput(
            name="temperature",
            display_name="Temperature",
            value=0.75,
            range_spec=RangeSpec(min=0, max=2, step=0.01),
            info="Controls randomness. Lower values are more deterministic, higher values are more creative.",
            advanced=True,
        ),
    ]

    def build_model(self) -> LanguageModel:  # type: ignore[type-var]
        """构建 Cohere Chat 模型实例。

        契约：`temperature` 不传则回退到默认值。
        副作用：实例化 `ChatCohere`。
        失败语义：初始化失败由下游异常抛出。
        决策：`temperature` 为空时使用 0.75。
        问题：部分 UI 可能返回空值导致模型构建失败。
        方案：设置默认回退值。
        代价：无法区分用户未设置与显式设空。
        重评：当配置层保证必填时。
        """
        cohere_api_key = self.cohere_api_key
        temperature = self.temperature

        api_key = SecretStr(cohere_api_key).get_secret_value() if cohere_api_key else None

        return ChatCohere(
            temperature=temperature or 0.75,
            cohere_api_key=api_key,
        )
