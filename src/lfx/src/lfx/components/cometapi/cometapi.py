"""
模块名称：`CometAPI` 文本模型组件

本模块提供 `CometAPI` 文本模型组件，主要用于从 `CometAPI` 拉取模型列表并构建 `ChatOpenAI` 实例。
主要功能包括：
- 拉取并缓存可用模型列表
- 校验模型选择并构建模型实例
- 支持 `JSON` 输出模式与随机种子

关键组件：
- `CometAPIComponent`

设计背景：统一 `CometAPI` 模型接入入口，保持与 LangFlow 组件接口一致。
注意事项：模型列表请求失败会回退到默认列表；未选择模型会抛 `ValueError`。
"""

import json

import requests
from langchain_openai import ChatOpenAI
from pydantic.v1 import SecretStr
from typing_extensions import override

from lfx.base.models.cometapi_constants import MODEL_NAMES
from lfx.base.models.model import LCModelComponent
from lfx.field_typing import LanguageModel
from lfx.field_typing.range_spec import RangeSpec
from lfx.inputs.inputs import (
    BoolInput,
    DictInput,
    DropdownInput,
    IntInput,
    SecretStrInput,
    SliderInput,
    StrInput,
)


class CometAPIComponent(LCModelComponent):
    """`CometAPI` 文本模型组件

    契约：
    - 输入：`api_key`、模型名、温度、最大 token 等配置
    - 输出：`ChatOpenAI` 语言模型实例
    - 副作用：可能触发模型列表请求与状态更新
    - 失败语义：构建失败时抛 `ValueError`
    """

    display_name = "CometAPI"
    description = "All AI Models in One API 500+ AI Models"
    icon = "CometAPI"
    name = "CometAPIModel"

    inputs = [
        *LCModelComponent.get_base_inputs(),
        SecretStrInput(
            name="api_key",
            display_name="CometAPI Key",
            required=True,
            info="Your CometAPI key",
            real_time_refresh=True,
        ),
        StrInput(
            name="app_name",
            display_name="App Name",
            info="Your app name for CometAPI rankings",
            advanced=True,
        ),
        DropdownInput(
            name="model_name",
            display_name="Model",
            info="The model to use for chat completion",
            options=["Select a model"],
            value="Select a model",
            real_time_refresh=True,
            required=True,
        ),
        DictInput(
            name="model_kwargs",
            display_name="Model Kwargs",
            info="Additional keyword arguments to pass to the model.",
            advanced=True,
        ),
        SliderInput(
            name="temperature",
            display_name="Temperature",
            value=0.7,
            range_spec=RangeSpec(min=0, max=2, step=0.01),
            info="Controls randomness. Lower values are more deterministic, higher values are more creative.",
            advanced=True,
        ),
        IntInput(
            name="max_tokens",
            display_name="Max Tokens",
            info="Maximum number of tokens to generate",
            advanced=True,
        ),
        IntInput(
            name="seed",
            display_name="Seed",
            info="Seed for reproducible outputs.",
            value=1,
            advanced=True,
        ),
        BoolInput(
            name="json_mode",
            display_name="JSON Mode",
            info="If enabled, the model will be asked to return a JSON object.",
            advanced=True,
        ),
    ]

    def get_models(self, token_override: str | None = None) -> list[str]:
        """拉取 `CometAPI` 可用模型列表

        关键路径（三步）：
        1) 组装请求 URL 与请求头
        2) 调用模型列表接口
        3) 解析返回并提取模型 `id`

        异常流：网络/解析失败时回退到默认模型列表。
        性能瓶颈：外部请求延迟。
        排障入口：`self.status` 记录请求失败信息。
        
        契约：
        - 输入：可选 `token_override`
        - 输出：模型 `id` 列表
        - 副作用：失败时更新 `self.status`
        - 失败语义：请求失败时返回 `MODEL_NAMES`
        """
        base_url = "https://api.cometapi.com/v1"
        url = f"{base_url}/models"

        headers = {"Content-Type": "application/json"}
        # 注意：当存在 `API` Key 时添加 `Bearer` 鉴权
        api_key_source = token_override if token_override else getattr(self, "api_key", None)
        if api_key_source:
            token = api_key_source.get_secret_value() if isinstance(api_key_source, SecretStr) else str(api_key_source)
            headers["Authorization"] = f"Bearer {token}"

        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            # 注意：JSON 解析失败时回退到默认模型列表
            try:
                model_list = response.json()
            except (json.JSONDecodeError, ValueError) as e:
                self.status = f"Error decoding models response: {e}"
                return MODEL_NAMES
            return [model["id"] for model in model_list.get("data", [])]
        except requests.RequestException as e:
            self.status = f"Error fetching models: {e}"
            return MODEL_NAMES

    @override
    def update_build_config(self, build_config: dict, field_value: str, field_name: str | None = None):
        """更新构建配置并刷新模型列表

        契约：
        - 输入：构建配置、字段值与字段名
        - 输出：更新后的构建配置
        - 副作用：可能触发模型列表请求
        - 失败语义：模型列表为空时保留占位项
        """
        if field_name == "api_key":
            models = self.get_models(field_value)
            model_cfg = build_config.get("model_name", {})
            # 注意：保留占位符（回退到现有值或默认提示）
            placeholder = model_cfg.get("placeholder", model_cfg.get("value", "Select a model"))
            current_value = model_cfg.get("value")

            options = list(models) if models else []
            # 注意：若当前值不在新列表中，仍保持可见
            if current_value and current_value not in options:
                options = [current_value, *options]

            model_cfg["options"] = options
            model_cfg["placeholder"] = placeholder
            build_config["model_name"] = model_cfg
        return build_config

    def build_model(self) -> LanguageModel:  # type: ignore[type-var]
        """构建 `ChatOpenAI` 模型实例

        关键路径（三步）：
        1) 校验模型选择并读取配置
        2) 构建 `ChatOpenAI` 实例
        3) 根据 `json_mode` 绑定响应格式

        异常流：未选择模型或构建失败时抛 `ValueError`。
        性能瓶颈：无显著性能瓶颈。
        排障入口：异常消息与 `self.status`。
        
        契约：
        - 输入：无（使用组件字段）
        - 输出：`LanguageModel` 实例
        - 副作用：可能启用 `json_mode`
        - 失败语义：构建失败时抛异常
        """
        api_key = self.api_key
        temperature = self.temperature
        model_name: str = self.model_name
        max_tokens = self.max_tokens
        model_kwargs = getattr(self, "model_kwargs", {}) or {}
        json_mode = self.json_mode
        seed = self.seed
        # 注意：必须选择有效模型
        if not model_name or model_name == "Select a model":
            msg = "Please select a valid CometAPI model."
            raise ValueError(msg)
        try:
            # 注意：安全提取原始 `API` Key
            _api_key = api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
            output = ChatOpenAI(
                model=model_name,
                api_key=_api_key or None,
                max_tokens=max_tokens or None,
                temperature=temperature,
                model_kwargs=model_kwargs,
                streaming=bool(self.stream),
                seed=seed,
                base_url="https://api.cometapi.com/v1",
            )
        except (TypeError, ValueError) as e:
            msg = "Could not connect to CometAPI."
            raise ValueError(msg) from e

        if json_mode:
            output = output.bind(response_format={"type": "json_object"})

        return output
