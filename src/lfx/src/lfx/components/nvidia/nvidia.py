"""NVIDIA LLM 组件。

本模块封装 `langchain_nvidia_ai_endpoints` 的 ChatNVIDIA 以生成文本。
主要功能包括：
- 从 NVIDIA API 拉取可用模型列表
- 构建 Langflow 模型输入参数
- 在配置变更时刷新模型下拉选项

注意事项：依赖 `langchain-nvidia-ai-endpoints`，并需要有效 `api_key`。
"""

from typing import Any

from lfx.base.models.model import LCModelComponent
from lfx.field_typing import LanguageModel
from lfx.field_typing.range_spec import RangeSpec
from lfx.inputs.inputs import BoolInput, DropdownInput, IntInput, MessageTextInput, SecretStrInput, SliderInput
from lfx.log.logger import logger
from lfx.schema.dotdict import dotdict


class NVIDIAModelComponent(LCModelComponent):
    """NVIDIA LLM 组件封装。

    契约：输入由 `inputs` 定义；输出为 `LanguageModel`。
    副作用：初始化时尝试拉取模型列表并可能记录日志。
    失败语义：依赖缺失抛 `ImportError`；模型拉取失败记录警告并降级为空列表。
    """

    display_name = "NVIDIA"
    description = "Generates text using NVIDIA LLMs."
    icon = "NVIDIA"

    try:
        import warnings

        # 注意：抑制特定版本重复的 NIM Key 警告，避免日志噪声
        warnings.filterwarnings("ignore", category=UserWarning, module="langchain_nvidia_ai_endpoints._common")
        from langchain_nvidia_ai_endpoints import ChatNVIDIA

        all_models = ChatNVIDIA().get_available_models()
    except ImportError as e:
        msg = "Please install langchain-nvidia-ai-endpoints to use the NVIDIA model."
        raise ImportError(msg) from e
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Failed to fetch NVIDIA models during initialization: {e}. Model list will be unavailable.")
        all_models = []

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
            info="The name of the NVIDIA model to use.",
            advanced=False,
            value=None,
            options=sorted(model.id for model in all_models),
            combobox=True,
            refresh_button=True,
        ),
        BoolInput(
            name="detailed_thinking",
            display_name="Detailed Thinking",
            info="If true, the model will return a detailed thought process. Only supported by reasoning models.",
            value=False,
            show=False,
        ),
        BoolInput(
            name="tool_model_enabled",
            display_name="Enable Tool Models",
            info="If enabled, only show models that support tool-calling.",
            advanced=False,
            value=False,
            real_time_refresh=True,
        ),
        MessageTextInput(
            name="base_url",
            display_name="NVIDIA Base URL",
            value="https://integrate.api.nvidia.com/v1",
            info="The base URL of the NVIDIA API. Defaults to https://integrate.api.nvidia.com/v1.",
        ),
        SecretStrInput(
            name="api_key",
            display_name="NVIDIA API Key",
            info="The NVIDIA API Key.",
            advanced=False,
            value="NVIDIA_API_KEY",
        ),
        SliderInput(
            name="temperature",
            display_name="Temperature",
            value=0.1,
            info="Run inference with this temperature.",
            range_spec=RangeSpec(min=0, max=1, step=0.01),
            advanced=True,
        ),
        IntInput(
            name="seed",
            display_name="Seed",
            info="The seed controls the reproducibility of the job.",
            advanced=True,
            value=1,
        ),
    ]

    def get_models(self, *, tool_model_enabled: bool | None = None) -> list[str]:
        """获取可用模型列表。

        契约：输入可选 `tool_model_enabled`；输出模型 ID 列表（已排序）。
        失败语义：依赖缺失抛 `ImportError`。
        """
        try:
            from langchain_nvidia_ai_endpoints import ChatNVIDIA
        except ImportError as e:
            msg = "Please install langchain-nvidia-ai-endpoints to use the NVIDIA model."
            raise ImportError(msg) from e

        # 注意：不使用旧模型缓存，避免 base_url 变化后出现不可用模型
        model = ChatNVIDIA(base_url=self.base_url, api_key=self.api_key)
        if tool_model_enabled:
            tool_models = [m for m in model.get_available_models() if m.supports_tools]
            return sorted(m.id for m in tool_models)
        return sorted(m.id for m in model.available_models)

    def update_build_config(self, build_config: dotdict, _field_value: Any, field_name: str | None = None):
        """根据字段变化刷新构建配置。

        契约：输入为 `build_config` 与字段名；输出更新后的 `build_config`。
        副作用：可能触发模型列表拉取并修改 `build_config`。
        失败语义：拉取模型失败抛 `ValueError`。
        """
        if field_name in {"model_name", "tool_model_enabled", "base_url", "api_key"}:
            try:
                ids = self.get_models(tool_model_enabled=self.tool_model_enabled)
                build_config["model_name"]["options"] = ids

                if "value" not in build_config["model_name"] or build_config["model_name"]["value"] is None:
                    build_config["model_name"]["value"] = ids[0]
                elif build_config["model_name"]["value"] not in ids:
                    build_config["model_name"]["value"] = None

                # TODO：后续通过 API 判断模型是否支持详细思考
                if build_config["model_name"]["value"] == "nemotron":
                    build_config["detailed_thinking"]["show"] = True
                else:
                    build_config["detailed_thinking"]["value"] = False
                    build_config["detailed_thinking"]["show"] = False
            except Exception as e:
                msg = f"Error getting model names: {e}"
                build_config["model_name"]["value"] = None
                build_config["model_name"]["options"] = []
                raise ValueError(msg) from e

        return build_config

    def build_model(self) -> LanguageModel:  # type: ignore[type-var]
        """构建 NVIDIA Chat 模型实例。

        契约：读取组件输入并返回 `ChatNVIDIA` 实例。
        失败语义：依赖缺失抛 `ImportError`。
        """
        try:
            from langchain_nvidia_ai_endpoints import ChatNVIDIA
        except ImportError as e:
            msg = "Please install langchain-nvidia-ai-endpoints to use the NVIDIA model."
            raise ImportError(msg) from e
        api_key = self.api_key
        temperature = self.temperature
        model_name: str = self.model_name
        max_tokens = self.max_tokens
        seed = self.seed
        return ChatNVIDIA(
            max_tokens=max_tokens or None,
            model=model_name,
            base_url=self.base_url,
            api_key=api_key,
            temperature=temperature or 0.1,
            seed=seed,
        )
