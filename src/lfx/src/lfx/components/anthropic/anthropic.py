"""
模块名称：`Anthropic` 模型组件

本模块提供 `AnthropicModelComponent`，用于通过 `Anthropic` `Messages API` 构建并调用模型。
主要功能包括：
- 构建 `ChatAnthropic` 模型实例
- 拉取模型列表并过滤工具调用能力
- 根据输入更新组件配置与模型下拉选项

关键组件：`AnthropicModelComponent`、`get_models`、`update_build_config`
设计背景：统一 `Anthropic` 模型接入与工具调用能力筛选
注意事项：依赖 `langchain_anthropic` 与 `anthropic`；`API Key` 为空时仅使用内置模型列表
"""

from typing import Any, cast

import requests
from pydantic import ValidationError

from lfx.base.models.anthropic_constants import (
    ANTHROPIC_MODELS,
    DEFAULT_ANTHROPIC_API_URL,
    TOOL_CALLING_SUPPORTED_ANTHROPIC_MODELS,
    TOOL_CALLING_UNSUPPORTED_ANTHROPIC_MODELS,
)
from lfx.base.models.model import LCModelComponent
from lfx.field_typing import LanguageModel
from lfx.field_typing.range_spec import RangeSpec
from lfx.io import BoolInput, DropdownInput, IntInput, MessageTextInput, SecretStrInput, SliderInput
from lfx.log.logger import logger
from lfx.schema.dotdict import dotdict


class AnthropicModelComponent(LCModelComponent):
    """`Anthropic` 模型组件。
    契约：输入为模型配置与认证参数；输出为 `LanguageModel` 实例。
    关键路径：读取输入 → 构建 `ChatAnthropic` → 返回模型。
    决策：使用 `Messages API` 接口。问题：统一模型调用路径；方案：`ChatAnthropic`；代价：依赖外部包；重评：当官方 `SDK` 接口变更时。
    """

    display_name = "Anthropic"
    description = "Generate text using Anthropic's Messages API and models."
    icon = "Anthropic"
    name = "AnthropicModel"

    inputs = [
        *LCModelComponent.get_base_inputs(),
        IntInput(
            name="max_tokens",
            display_name="Max Tokens",
            advanced=True,
            value=4096,
            info="The maximum number of tokens to generate. Set to 0 for unlimited tokens.",
        ),
        DropdownInput(
            name="model_name",
            display_name="Model Name",
            options=ANTHROPIC_MODELS,
            refresh_button=True,
            value=ANTHROPIC_MODELS[0],
            combobox=True,
        ),
        SecretStrInput(
            name="api_key",
            display_name="Anthropic API Key",
            info="Your Anthropic API key.",
            value=None,
            required=True,
            real_time_refresh=True,
        ),
        SliderInput(
            name="temperature",
            display_name="Temperature",
            value=0.1,
            info="Run inference with this temperature. Must by in the closed interval [0.0, 1.0].",
            range_spec=RangeSpec(min=0, max=1, step=0.01),
            advanced=True,
        ),
        MessageTextInput(
            name="base_url",
            display_name="Anthropic API URL",
            info="Endpoint of the Anthropic API. Defaults to 'https://api.anthropic.com' if not specified.",
            value=DEFAULT_ANTHROPIC_API_URL,
            real_time_refresh=True,
            advanced=True,
        ),
        BoolInput(
            name="tool_model_enabled",
            display_name="Enable Tool Models",
            info=(
                "Select if you want to use models that can work with tools. If yes, only those models will be shown."
            ),
            advanced=False,
            value=False,
            real_time_refresh=True,
        ),
    ]

    def build_model(self) -> LanguageModel:  # type: ignore[type-var]
        """构建 `ChatAnthropic` 模型实例。
        契约：返回 `LanguageModel`；缺少依赖或连接失败时抛异常。
        关键路径：导入 `ChatAnthropic` → 规范化 `max_tokens` → 创建实例。
        决策：空 `max_tokens` 兜底为 4096。问题：空值导致校验失败；方案：默认值；代价：可能偏大；重评：当默认策略调整时。
        """
        try:
            from langchain_anthropic.chat_models import ChatAnthropic
        except ImportError as e:
            msg = "langchain_anthropic is not installed. Please install it with `pip install langchain_anthropic`."
            raise ImportError(msg) from e
        try:
            max_tokens_value = getattr(self, "max_tokens", "")
            max_tokens_value = 4096 if max_tokens_value == "" else int(max_tokens_value)
            output = ChatAnthropic(
                model=self.model_name,
                anthropic_api_key=self.api_key,
                max_tokens=max_tokens_value,
                temperature=self.temperature,
                anthropic_api_url=self.base_url or DEFAULT_ANTHROPIC_API_URL,
                streaming=self.stream,
            )
        except ValidationError:
            raise
        except Exception as e:
            msg = "Could not connect to Anthropic API."
            raise ValueError(msg) from e

        return output

    def get_models(self, *, tool_model_enabled: bool | None = None) -> list[str]:
        """获取模型列表并按工具能力筛选。
        契约：返回模型 `ID` 列表；失败时回退到内置列表。
        关键路径：`SDK` 拉取 → 合并内置列表 → 可选工具能力过滤。
        决策：接口失败回退到静态列表。问题：可用性优先；方案：回退；代价：模型列表可能过期；重评：当接口稳定且可用时。
        """
        try:
            import anthropic

            client = anthropic.Anthropic(api_key=self.api_key)
            models = client.models.list(limit=20).data
            model_ids = ANTHROPIC_MODELS + [model.id for model in models]
        except (ImportError, ValueError, requests.exceptions.RequestException) as e:
            logger.exception(f"Error getting model names: {e}")
            model_ids = ANTHROPIC_MODELS

        if tool_model_enabled:
            try:
                from langchain_anthropic.chat_models import ChatAnthropic
            except ImportError as e:
                msg = "langchain_anthropic is not installed. Please install it with `pip install langchain_anthropic`."
                raise ImportError(msg) from e

            # 注意：使用新列表避免遍历时修改原列表。
            filtered_models = []
            for model in model_ids:
                if model in TOOL_CALLING_SUPPORTED_ANTHROPIC_MODELS:
                    filtered_models.append(model)
                    continue

                model_with_tool = ChatAnthropic(
                    model=model,  # 注意：使用当前遍历的模型进行检测
                    anthropic_api_key=self.api_key,
                    anthropic_api_url=cast("str", self.base_url) or DEFAULT_ANTHROPIC_API_URL,
                )

            if (
                not self.supports_tool_calling(model_with_tool)
                or model in TOOL_CALLING_UNSUPPORTED_ANTHROPIC_MODELS
            ):
                continue

                filtered_models.append(model)

            return filtered_models

        return model_ids

    def _get_exception_message(self, exception: Exception) -> str | None:
    """从 `Anthropic` 异常中提取错误消息。
        契约：仅处理 `BadRequestError`，无匹配则返回 `None`。
        关键路径：导入异常类型 → 匹配类型 → 读取 `body.message`。
        决策：仅抽取 `BadRequestError`。问题：其他异常结构不稳定；方案：聚焦常见错误；代价：信息可能缺失；重评：当 `SDK` 异常类型稳定时。
        """
        try:
            from anthropic import BadRequestError
        except ImportError:
            return None
        if isinstance(exception, BadRequestError):
            message = exception.body.get("error", {}).get("message")
            if message:
                return message
        return None

    def update_build_config(self, build_config: dotdict, field_value: Any, field_name: str | None = None):
        """根据字段变更更新 build_config。
        契约：返回更新后的 `build_config`；模型列表获取失败时抛 `ValueError`。
        关键路径：修正 `base_url` → 判断触发字段 → 拉取/回退模型列表 → 更新下拉选项。
        决策：`API Key` 为空时仅使用内置模型。问题：避免无凭证调用远端；方案：回退静态列表；代价：列表可能不全；重评：当允许匿名查询时。
        """
        if "base_url" in build_config and build_config["base_url"]["value"] is None:
            build_config["base_url"]["value"] = DEFAULT_ANTHROPIC_API_URL
            self.base_url = DEFAULT_ANTHROPIC_API_URL
        if field_name in {"base_url", "model_name", "tool_model_enabled", "api_key"} and field_value:
            try:
                if len(self.api_key) == 0:
                    ids = ANTHROPIC_MODELS
                else:
                    try:
                        ids = self.get_models(tool_model_enabled=self.tool_model_enabled)
                    except (ImportError, ValueError, requests.exceptions.RequestException) as e:
                        logger.exception(f"Error getting model names: {e}")
                        ids = ANTHROPIC_MODELS
                build_config.setdefault("model_name", {})
                build_config["model_name"]["options"] = ids
                build_config["model_name"].setdefault("value", ids[0])
                build_config["model_name"]["combobox"] = True
            except Exception as e:
                msg = f"Error getting model names: {e}"
                raise ValueError(msg) from e
        return build_config
