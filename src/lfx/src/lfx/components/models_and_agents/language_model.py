"""
模块名称：Language Model 组件

本模块提供语言模型组件的配置与实例化逻辑，支持多 provider 与流式输出设置。
主要功能：
- 构建语言模型实例并透传温度、流式等参数；
- 动态更新构建配置并切换厂商特定字段显示。

关键组件：
- LanguageModelComponent：模型组件入口。

设计背景：统一 LLM 接入形态，降低不同 provider 参数差异带来的配置复杂度。
注意事项：Ollama/WatsonX 需要额外字段，且默认地址需可覆盖。
"""

from lfx.base.models.model import LCModelComponent
from lfx.base.models.unified_models import (
    get_language_model_options,
    get_llm,
    update_model_options_in_build_config,
)
from lfx.base.models.watsonx_constants import IBM_WATSONX_URLS
from lfx.field_typing import LanguageModel
from lfx.field_typing.range_spec import RangeSpec
from lfx.inputs.inputs import BoolInput, DropdownInput, StrInput
from lfx.io import MessageInput, ModelInput, MultilineInput, SecretStrInput, SliderInput

DEFAULT_OLLAMA_URL = "http://localhost:11434"


class LanguageModelComponent(LCModelComponent):
    """语言模型组件封装

    契约：`model` 必须选择有效 provider；返回 `LanguageModel` 实例或抛异常。
    关键路径：1) 读取模型配置 2) 透传运行参数 3) 调用统一构建入口 `get_llm`。
    决策：使用统一构建函数而非在组件中分支实例化
    问题：各 provider 初始化差异较大
    方案：委托给统一的 `get_llm` 进行适配
    代价：组件对底层差异的可见性降低
    重评：当需要组件级别精细化初始化时
    """

    display_name = "Language Model"
    description = "Runs a language model given a specified provider."
    documentation: str = "https://docs.langflow.org/components-models"
    icon = "brain-circuit"
    category = "models"
    priority = 0  # 注意：优先级置 0 以在列表中靠前展示。

    inputs = [
        ModelInput(
            name="model",
            display_name="Language Model",
            info="Select your model provider",
            real_time_refresh=True,
            required=True,
        ),
        SecretStrInput(
            name="api_key",
            display_name="API Key",
            info="Model Provider API key",
            required=False,
            show=True,
            real_time_refresh=True,
            advanced=True,
        ),
        DropdownInput(
            name="base_url_ibm_watsonx",
            display_name="watsonx API Endpoint",
            info="The base URL of the API (IBM watsonx.ai only)",
            options=IBM_WATSONX_URLS,
            value=IBM_WATSONX_URLS[0],
            show=False,
            real_time_refresh=True,
        ),
        StrInput(
            name="project_id",
            display_name="watsonx Project ID",
            info="The project ID associated with the foundation model (IBM watsonx.ai only)",
            show=False,
            required=False,
        ),
        MessageInput(
            name="ollama_base_url",
            display_name="Ollama API URL",
            info=f"Endpoint of the Ollama API (Ollama only). Defaults to {DEFAULT_OLLAMA_URL}",
            value=DEFAULT_OLLAMA_URL,
            show=False,
            real_time_refresh=True,
            load_from_db=True,
        ),
        MessageInput(
            name="input_value",
            display_name="Input",
            info="The input text to send to the model",
        ),
        MultilineInput(
            name="system_message",
            display_name="System Message",
            info="A system message that helps set the behavior of the assistant",
            advanced=False,
        ),
        BoolInput(
            name="stream",
            display_name="Stream",
            info="Whether to stream the response",
            value=False,
            advanced=True,
        ),
        SliderInput(
            name="temperature",
            display_name="Temperature",
            value=0.1,
            info="Controls randomness in responses",
            range_spec=RangeSpec(min=0, max=1, step=0.01),
            advanced=True,
        ),
    ]

    def build_model(self) -> LanguageModel:
        """构建语言模型实例

        契约：依赖 `model/api_key` 等输入；返回可调用的 LanguageModel 实例。
        关键路径：1) 读取基础参数 2) 注入 provider 特定字段 3) 调用 `get_llm`。
        异常流：`get_llm` 可能抛 `ValueError`/配置错误，由调用方处理。
        决策：将实例化委托给 `get_llm`
        问题：多 provider 初始化差异大
        方案：统一入口集中处理
        代价：组件层难以微调初始化细节
        重评：当需要组件级别精细控制时
        """
        return get_llm(
            model=self.model,
            user_id=self.user_id,
            api_key=self.api_key,
            temperature=self.temperature,
            stream=self.stream,
            watsonx_url=getattr(self, "base_url_ibm_watsonx", None),
            watsonx_project_id=getattr(self, "project_id", None),
            ollama_base_url=getattr(self, "ollama_base_url", None),
        )

    def update_build_config(self, build_config: dict, field_value: str, field_name: str | None = None):
        """更新构建配置并切换厂商字段显隐

        契约：输入 `build_config` 可被就地修改；返回更新后的配置。
        关键路径：1) 刷新模型选项 2) 识别 provider 3) 显示/隐藏特定字段。
        异常流：未选模型时保持默认显示，不抛异常。
        决策：在构建配置阶段处理字段显隐
        问题：不同 provider 的额外字段易误填
        方案：基于选中模型动态显示
        代价：切换模型时字段状态变化，可能导致旧值不再生效
        重评：当 UI 支持 schema 驱动时
        """
        # 实现：刷新模型选项列表。
        build_config = update_model_options_in_build_config(
            component=self,
            build_config=build_config,
            cache_key_prefix="language_model_options",
            get_options_func=get_language_model_options,
            field_name=field_name,
            field_value=field_value,
        )

        # 注意：根据 provider 切换特定字段显示状态。
        # 实现：优先使用当前变更值，否则回退到 build_config。
        current_model_value = field_value if field_name == "model" else build_config.get("model", {}).get("value")
        if isinstance(current_model_value, list) and len(current_model_value) > 0:
            selected_model = current_model_value[0]
            provider = selected_model.get("provider", "")

            # 注意：仅在 WatsonX 时展示相关字段。
            is_watsonx = provider == "IBM WatsonX"
            build_config["base_url_ibm_watsonx"]["show"] = is_watsonx
            build_config["project_id"]["show"] = is_watsonx
            build_config["base_url_ibm_watsonx"]["required"] = is_watsonx
            build_config["project_id"]["required"] = is_watsonx

            # 注意：仅在 Ollama 时展示相关字段。
            is_ollama = provider == "Ollama"
            build_config["ollama_base_url"]["show"] = is_ollama

        return build_config
