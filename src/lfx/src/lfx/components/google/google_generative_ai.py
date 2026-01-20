"""
模块名称：`Google Generative AI` 模型组件

本模块提供 `GoogleGenerativeAIComponent`，用于构建并调用 `Google Generative AI` 模型。
主要功能包括：
- 构建 `ChatGoogleGenerativeAIFixed` 实例
- 拉取可用模型并支持工具调用筛选
- 更新组件配置与模型下拉选项

关键组件：`GoogleGenerativeAIComponent`
设计背景：统一 `Google Generative AI` 模型接入与工具能力筛选
注意事项：依赖 `langchain_google_genai`；`API Key` 为空时回退默认模型列表
"""

from typing import Any

import requests
from pydantic.v1 import SecretStr

from lfx.base.models.google_generative_ai_constants import GOOGLE_GENERATIVE_AI_MODELS
from lfx.base.models.google_generative_ai_model import ChatGoogleGenerativeAIFixed
from lfx.base.models.model import LCModelComponent
from lfx.field_typing import LanguageModel
from lfx.field_typing.range_spec import RangeSpec
from lfx.inputs.inputs import BoolInput, DropdownInput, FloatInput, IntInput, SecretStrInput, SliderInput
from lfx.log.logger import logger
from lfx.schema.dotdict import dotdict


class GoogleGenerativeAIComponent(LCModelComponent):
    """`Google Generative AI` 模型组件。
    契约：输入为模型与采样参数；输出为 `LanguageModel` 实例。
    关键路径：读取输入 → 构建模型实例 → 返回。
    决策：使用修正的 `ChatGoogleGenerativeAIFixed`。问题：上游对多函数支持不足；方案：临时替代类；代价：维护成本；重评：当上游修复时。
    """

    display_name = "Google Generative AI"
    description = "Generate text using Google Generative AI."
    icon = "GoogleGenerativeAI"
    name = "GoogleGenerativeAIModel"

    inputs = [
        *LCModelComponent.get_base_inputs(),
        IntInput(
            name="max_output_tokens", display_name="Max Output Tokens", info="The maximum number of tokens to generate."
        ),
        DropdownInput(
            name="model_name",
            display_name="Model",
            info="The name of the model to use.",
            options=GOOGLE_GENERATIVE_AI_MODELS,
            value="gemini-1.5-pro",
            refresh_button=True,
            combobox=True,
        ),
        SecretStrInput(
            name="api_key",
            display_name="Google API Key",
            info="The Google API Key to use for the Google Generative AI.",
            required=True,
            real_time_refresh=True,
        ),
        FloatInput(
            name="top_p",
            display_name="Top P",
            info="The maximum cumulative probability of tokens to consider when sampling.",
            advanced=True,
        ),
        SliderInput(
            name="temperature",
            display_name="Temperature",
            value=0.1,
            range_spec=RangeSpec(min=0, max=1, step=0.01),
            info="Controls randomness. Lower values are more deterministic, higher values are more creative.",
        ),
        IntInput(
            name="n",
            display_name="N",
            info="Number of chat completions to generate for each prompt. "
            "Note that the API may not return the full n completions if duplicates are generated.",
            advanced=True,
        ),
        IntInput(
            name="top_k",
            display_name="Top K",
            info="Decode using top-k sampling: consider the set of top_k most probable tokens. Must be positive.",
            advanced=True,
        ),
        BoolInput(
            name="tool_model_enabled",
            display_name="Tool Model Enabled",
            info="Whether to use the tool model.",
            value=False,
        ),
    ]

    def build_model(self) -> LanguageModel:  # type: ignore[type-var]
        """构建模型实例。
        契约：返回 `LanguageModel`；`API Key` 无效时抛异常。
        关键路径：读取参数 → 规范化为空值 → 初始化模型。
    决策：空参数用 `None` 或默认值。问题：`API` 不接受空字符串；方案：空值转换；代价：隐藏输入错误；重评：当需要强校验时。
        """
        google_api_key = self.api_key
        model = self.model_name
        max_output_tokens = self.max_output_tokens
        temperature = self.temperature
        top_k = self.top_k
        top_p = self.top_p
        n = self.n

        # 注意：使用修正类以支持多函数调用。
        # TODO：上游修复后考虑移除
        return ChatGoogleGenerativeAIFixed(
            model=model,
            max_output_tokens=max_output_tokens or None,
            temperature=temperature,
            top_k=top_k or None,
            top_p=top_p or None,
            n=n or 1,
            google_api_key=SecretStr(google_api_key).get_secret_value(),
        )

    def get_models(self, *, tool_model_enabled: bool | None = None) -> list[str]:
        """获取模型列表并按工具能力筛选。
        契约：返回模型 `ID` 列表；失败时回退到默认列表。
        关键路径：`SDK` 拉取 → 过滤支持 `generateContent` → 可选工具筛选。
        决策：接口失败回退到静态列表。问题：可用性优先；方案：回退；代价：列表可能过期；重评：当 `API` 稳定时。
        """
        try:
            import google.generativeai as genai

            genai.configure(api_key=self.api_key)
            model_ids = [
                model.name.replace("models/", "")
                for model in genai.list_models()
                if "generateContent" in model.supported_generation_methods
            ]
            model_ids.sort(reverse=True)
        except (ImportError, ValueError) as e:
            logger.exception(f"Error getting model names: {e}")
            model_ids = GOOGLE_GENERATIVE_AI_MODELS
        if tool_model_enabled:
            try:
                from langchain_google_genai.chat_models import ChatGoogleGenerativeAI
            except ImportError as e:
                msg = "langchain_google_genai is not installed."
                raise ImportError(msg) from e
            for model in model_ids:
                model_with_tool = ChatGoogleGenerativeAI(
                    model=self.model_name,
                    google_api_key=self.api_key,
                )
                if not self.supports_tool_calling(model_with_tool):
                    model_ids.remove(model)
        return model_ids

    def update_build_config(self, build_config: dotdict, field_value: Any, field_name: str | None = None):
        """根据字段变更更新 build_config。
        契约：返回更新后的 `build_config`；模型列表获取失败时抛 `ValueError`。
        关键路径：判断触发字段 → 拉取/回退模型列表 → 更新下拉选项。
        决策：`API Key` 为空时使用默认列表。问题：避免匿名请求失败；方案：静态回退；代价：列表可能不全；重评：当支持匿名查询时。
        """
        if field_name in {"base_url", "model_name", "tool_model_enabled", "api_key"} and field_value:
            try:
                if len(self.api_key) == 0:
                    ids = GOOGLE_GENERATIVE_AI_MODELS
                else:
                    try:
                        ids = self.get_models(tool_model_enabled=self.tool_model_enabled)
                    except (ImportError, ValueError, requests.exceptions.RequestException) as e:
                        logger.exception(f"Error getting model names: {e}")
                        ids = GOOGLE_GENERATIVE_AI_MODELS
                build_config.setdefault("model_name", {})
                build_config["model_name"]["options"] = ids
                build_config["model_name"].setdefault("value", ids[0])
            except Exception as e:
                msg = f"Error getting model names: {e}"
                raise ValueError(msg) from e
        return build_config
