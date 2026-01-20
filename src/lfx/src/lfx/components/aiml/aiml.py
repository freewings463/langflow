"""
模块名称：`AIML` 文本模型组件

本模块提供基于 `AI/ML API` 的文本生成组件，主要用于将配置映射为 `ChatOpenAI` 模型实例。
主要功能包括：
- 拉取并刷新可用模型列表
- 组装模型参数并构建 `ChatOpenAI` 实例
- 解析部分 `OpenAI` 错误以提取用户可读信息

关键组件：
- `AIMLModelComponent`

设计背景：统一 `AI/ML API` 模型配置入口，保持与 LangFlow 组件接口一致。
注意事项：`o1` 模型温度参数需特殊处理；无效配置会在模型构建阶段暴露异常。
"""

from langchain_openai import ChatOpenAI
from pydantic.v1 import SecretStr
from typing_extensions import override

from lfx.base.models.aiml_constants import AimlModels
from lfx.base.models.model import LCModelComponent
from lfx.field_typing import LanguageModel
from lfx.field_typing.range_spec import RangeSpec
from lfx.inputs.inputs import (
    DictInput,
    DropdownInput,
    IntInput,
    SecretStrInput,
    SliderInput,
    StrInput,
)


class AIMLModelComponent(LCModelComponent):
    """`AI/ML API` 文本模型组件

    契约：
    - 输入：模型名、`API` Key、温度、最大 token 等配置
    - 输出：`ChatOpenAI` 语言模型实例
    - 副作用：可能触发模型列表刷新
    - 失败语义：构建失败时抛出底层异常
    """
    display_name = "AI/ML API"
    description = "Generates text using AI/ML API LLMs."
    icon = "AIML"
    name = "AIMLModel"
    documentation = "https://docs.aimlapi.com/api-reference"

    inputs = [
        *LCModelComponent.get_base_inputs(),
        IntInput(
            name="max_tokens",
            display_name="Max Tokens",
            advanced=True,
            info="The maximum number of tokens to generate. Set to 0 for unlimited tokens.",
            range_spec=RangeSpec(min=0, max=128000),
        ),
        DictInput(name="model_kwargs", display_name="Model Kwargs", advanced=True),
        DropdownInput(
            name="model_name",
            display_name="Model Name",
            advanced=False,
            options=[],
            refresh_button=True,
        ),
        StrInput(
            name="aiml_api_base",
            display_name="AI/ML API Base",
            advanced=True,
            info="The base URL of the API. Defaults to https://api.aimlapi.com . "
            "You can change this to use other APIs like JinaChat, LocalAI and Prem.",
        ),
        SecretStrInput(
            name="api_key",
            display_name="AI/ML API Key",
            info="The AI/ML API Key to use for the OpenAI model.",
            advanced=False,
            value="AIML_API_KEY",
            required=True,
        ),
        SliderInput(
            name="temperature",
            display_name="Temperature",
            value=0.1,
            range_spec=RangeSpec(min=0, max=2, step=0.01),
        ),
    ]

    @override
    def update_build_config(self, build_config: dict, field_value: str, field_name: str | None = None):
        """更新构建配置并刷新模型列表

        契约：
        - 输入：构建配置、字段值与字段名
        - 输出：更新后的构建配置
        - 副作用：调用 `AimlModels.get_aiml_models` 刷新模型列表
        - 失败语义：模型拉取失败时可能抛异常
        """
        if field_name in {"api_key", "aiml_api_base", "model_name"}:
            aiml = AimlModels()
            aiml.get_aiml_models()
            build_config["model_name"]["options"] = aiml.chat_models
        return build_config

    def build_model(self) -> LanguageModel:  # type: ignore[type-var]
        """构建 `ChatOpenAI` 模型实例

        关键路径（三步）：
        1) 读取组件字段与默认值
        2) 处理 `o1` 模型温度兼容逻辑
        3) 组装并返回 `ChatOpenAI` 实例

        异常流：无效配置会在模型初始化时抛出异常。
        性能瓶颈：无显著性能瓶颈。
        排障入口：底层 `OpenAI`/网络异常日志。
        
        契约：
        - 输入：无（使用组件字段）
        - 输出：`LanguageModel` 实例
        - 副作用：无
        - 失败语义：构建失败时抛出异常
        """
        aiml_api_key = self.api_key
        temperature = self.temperature
        model_name: str = self.model_name
        max_tokens = self.max_tokens
        model_kwargs = self.model_kwargs or {}
        aiml_api_base = self.aiml_api_base or "https://api.aimlapi.com/v2"

        openai_api_key = aiml_api_key.get_secret_value() if isinstance(aiml_api_key, SecretStr) else aiml_api_key

        # 注意：`OpenAI` 修复 `o1` 温度参数前需强制设为 `1`
        if "o1" in model_name:
            temperature = 1

        return ChatOpenAI(
            model=model_name,
            temperature=temperature,
            api_key=openai_api_key,
            base_url=aiml_api_base,
            max_tokens=max_tokens or None,
            **model_kwargs,
        )

    def _get_exception_message(self, e: Exception):
        """从 `OpenAI` 异常中提取可读消息

        契约：
        - 输入：异常对象
        - 输出：错误消息字符串或 `None`
        - 副作用：无
        - 失败语义：无匹配错误类型时返回 `None`
        """
        try:
            from openai.error import BadRequestError
        except ImportError:
            return None
        if isinstance(e, BadRequestError):
            message = e.json_body.get("error", {}).get("message", "")
            if message:
                return message
        return None
