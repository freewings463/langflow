"""模块名称：SambaNova LLM 组件适配

本模块提供 SambaNova Cloud 的 Langflow 组件封装，用于生成文本。
使用场景：在对话流中调用 SambaNova 云端模型。
主要功能包括：
- 构建 `ChatSambaNovaCloud` 实例并传入配置
- 处理 API Key 与默认参数

关键组件：
- SambaNovaComponent：SambaNova 模型组件入口

设计背景：复用 Langflow 的模型组件接口，统一参数与调用方式
注意事项：`api_key` 为必填；`base_url` 可指向 Sambastudio 等私有部署
"""

from langchain_sambanova import ChatSambaNovaCloud
from pydantic.v1 import SecretStr

from lfx.base.models.model import LCModelComponent
from lfx.base.models.sambanova_constants import SAMBANOVA_MODEL_NAMES
from lfx.field_typing import LanguageModel
from lfx.field_typing.range_spec import RangeSpec
from lfx.io import DropdownInput, IntInput, SecretStrInput, SliderInput, StrInput


class SambaNovaComponent(LCModelComponent):
    """SambaNova 模型组件，封装模型与参数。

    契约：输入 `api_key`/`model_name`/`max_tokens` 等，输出 `LanguageModel`
    关键路径：1) 读取输入参数 2) 处理密钥 3) 初始化 `ChatSambaNovaCloud`
    副作用：可能进行网络初始化
    异常流：底层 SDK 异常直接上抛
    排障入口：SambaNova SDK 抛错消息
    决策：默认参数回退到安全值
    问题：用户可能未设置 `max_tokens`/`temperature`
    方案：`max_tokens` 缺省回退到 1024，`temperature` 回退到 0.07
    代价：默认值可能与用户预期不一致
    重评：当产品有明确默认策略或 UI 强制设置时
    """
    display_name = "SambaNova"
    description = "Generate text using Sambanova LLMs."
    documentation = "https://cloud.sambanova.ai/"
    icon = "SambaNova"
    name = "SambaNovaModel"

    inputs = [
        *LCModelComponent.get_base_inputs(),
        StrInput(
            name="base_url",
            display_name="SambaNova Cloud Base Url",
            advanced=True,
            info="The base URL of the Sambanova Cloud API. "
            "Defaults to https://api.sambanova.ai/v1/chat/completions. "
            "You can change this to use other urls like Sambastudio",
        ),
        DropdownInput(
            name="model_name",
            display_name="Model Name",
            advanced=False,
            options=SAMBANOVA_MODEL_NAMES,
            value=SAMBANOVA_MODEL_NAMES[0],
        ),
        SecretStrInput(
            name="api_key",
            display_name="Sambanova API Key",
            info="The Sambanova API Key to use for the Sambanova model.",
            advanced=False,
            value="SAMBANOVA_API_KEY",
            required=True,
        ),
        IntInput(
            name="max_tokens",
            display_name="Max Tokens",
            advanced=True,
            value=2048,
            info="The maximum number of tokens to generate.",
        ),
        SliderInput(
            name="top_p",
            display_name="top_p",
            advanced=True,
            value=1.0,
            range_spec=RangeSpec(min=0, max=1, step=0.01),
            info="Model top_p",
        ),
        SliderInput(
            name="temperature",
            display_name="Temperature",
            value=0.1,
            range_spec=RangeSpec(min=0, max=2, step=0.01),
            advanced=True,
        ),
    ]

    def build_model(self) -> LanguageModel:  # type: ignore[type-var]
        """构建 SambaNova Chat 模型实例。

        关键路径（三步）：
        1) 汇总输入参数
        2) 处理 `api_key` 为明文
        3) 初始化 `ChatSambaNovaCloud`

        契约：返回 `LanguageModel`
        副作用：可能进行网络初始化
        异常流：底层 SDK 异常直接上抛
        """
        sambanova_url = self.base_url
        sambanova_api_key = self.api_key
        model_name = self.model_name
        max_tokens = self.max_tokens
        top_p = self.top_p
        temperature = self.temperature

        # 注意：`SecretStr` 仅在此处解密，避免在其他层泄漏。
        api_key = SecretStr(sambanova_api_key).get_secret_value() if sambanova_api_key else None

        return ChatSambaNovaCloud(
            model=model_name,
            max_tokens=max_tokens or 1024,
            temperature=temperature or 0.07,
            top_p=top_p,
            sambanova_url=sambanova_url,
            sambanova_api_key=api_key,
        )
