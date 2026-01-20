"""
模块名称：LiteLLM 模型组件（已停用）

本模块提供基于 LiteLLM 的聊天模型组件，主要用于将多家厂商模型统一为 LangChain `ChatLiteLLM` 接口。主要功能包括：
- 组装模型配置并实例化 `ChatLiteLLM`
- 处理 Azure 等 Provider 的必需参数校验

关键组件：
- `ChatLiteLLMModelComponent`：聊天模型组件

设计背景：历史上用于整合 LiteLLM 多厂商模型接入，现标记为 legacy。
注意事项：依赖 `litellm` 包；未设置必需字段时会抛出异常。
"""

from langchain_community.chat_models.litellm import ChatLiteLLM, ChatLiteLLMException

from lfx.base.constants import STREAM_INFO_TEXT
from lfx.base.models.model import LCModelComponent
from lfx.field_typing import LanguageModel
from lfx.io import (
    BoolInput,
    DictInput,
    DropdownInput,
    FloatInput,
    IntInput,
    MessageInput,
    SecretStrInput,
    StrInput,
)


class ChatLiteLLMModelComponent(LCModelComponent):
    """LiteLLM 聊天模型组件。

    契约：`model` 与 `provider` 必须匹配；Azure 需补齐 `api_base` 与 `api_version`。
    失败语义：依赖缺失抛 `ChatLiteLLMException`；参数缺失抛 `ValueError`。
    副作用：配置全局 `litellm` 参数并创建模型实例。
    """

    display_name = "LiteLLM"
    description = "`LiteLLM` collection of large language models."
    documentation = "https://python.langchain.com/docs/integrations/chat/litellm"
    icon = "🚄"

    inputs = [
        MessageInput(name="input_value", display_name="Input"),
        StrInput(
            name="model",
            display_name="Model name",
            advanced=False,
            required=True,
            info="The name of the model to use. For example, `gpt-3.5-turbo`.",
        ),
        SecretStrInput(
            name="api_key",
            display_name="Chat LiteLLM API Key",
            advanced=False,
            required=False,
        ),
        DropdownInput(
            name="provider",
            display_name="Provider",
            info="The provider of the API key.",
            options=[
                "OpenAI",
                "Azure",
                "Anthropic",
                "Replicate",
                "Cohere",
                "OpenRouter",
            ],
        ),
        FloatInput(
            name="temperature",
            display_name="Temperature",
            advanced=False,
            required=False,
            value=0.7,
        ),
        DictInput(
            name="kwargs",
            display_name="Kwargs",
            advanced=True,
            required=False,
            is_list=True,
            value={},
        ),
        DictInput(
            name="model_kwargs",
            display_name="Model kwargs",
            advanced=True,
            required=False,
            is_list=True,
            value={},
        ),
        FloatInput(name="top_p", display_name="Top p", advanced=True, required=False, value=0.5),
        IntInput(name="top_k", display_name="Top k", advanced=True, required=False, value=35),
        IntInput(
            name="n",
            display_name="N",
            advanced=True,
            required=False,
            info="Number of chat completions to generate for each prompt. "
            "Note that the API may not return the full n completions if duplicates are generated.",
            value=1,
        ),
        IntInput(
            name="max_tokens",
            display_name="Max tokens",
            advanced=False,
            value=256,
            info="The maximum number of tokens to generate for each chat completion.",
        ),
        IntInput(
            name="max_retries",
            display_name="Max retries",
            advanced=True,
            required=False,
            value=6,
        ),
        BoolInput(
            name="verbose",
            display_name="Verbose",
            advanced=True,
            required=False,
            value=False,
        ),
        BoolInput(
            name="stream",
            display_name="Stream",
            info=STREAM_INFO_TEXT,
            advanced=True,
        ),
        StrInput(
            name="system_message",
            display_name="System Message",
            info="System message to pass to the model.",
            advanced=True,
        ),
    ]

    def build_model(self) -> LanguageModel:  # type: ignore[type-var]
        """构建 LiteLLM 模型实例。

        契约：返回 `ChatLiteLLM`，其 `client.api_key` 使用组件输入。
        失败语义：依赖缺失抛 `ChatLiteLLMException`；Azure 参数缺失抛 `ValueError`。
        副作用：设置 `litellm.drop_params` 与 `litellm.set_verbose`。

        关键路径（三步）：
        1) 导入并配置 `litellm`
        2) 清理空参数并校验 Azure 必需字段
        3) 构建模型实例并注入 API Key
        """
        try:
            import litellm

            litellm.drop_params = True
            litellm.set_verbose = self.verbose
        except ImportError as e:
            msg = "Could not import litellm python package. Please install it with `pip install litellm`"
            raise ChatLiteLLMException(msg) from e
        # 注意：移除空键，避免请求参数污染
        if "" in self.kwargs:
            del self.kwargs[""]
        if "" in self.model_kwargs:
            del self.model_kwargs[""]
        # 注意：Azure provider 必需字段缺失时直接抛错
        if self.provider == "Azure":
            if "api_base" not in self.kwargs:
                msg = "Missing api_base on kwargs"
                raise ValueError(msg)
            if "api_version" not in self.model_kwargs:
                msg = "Missing api_version on model_kwargs"
                raise ValueError(msg)
        output = ChatLiteLLM(
            model=f"{self.provider.lower()}/{self.model}",
            client=None,
            streaming=self.stream,
            temperature=self.temperature,
            model_kwargs=self.model_kwargs if self.model_kwargs is not None else {},
            top_p=self.top_p,
            top_k=self.top_k,
            n=self.n,
            max_tokens=self.max_tokens,
            max_retries=self.max_retries,
            **self.kwargs,
        )
        output.client.api_key = self.api_key

        return output
