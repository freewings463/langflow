"""
模块名称：Embedding 模型组件

本模块封装 Embedding 模型的选择与实例化逻辑，支持多厂商参数映射与动态配置。
主要功能：
- 根据用户选择的模型与 provider 构建 Embeddings 实例；
- 动态调整构建配置以展示厂商特定字段；
- 统一参数映射并处理厂商差异。

关键组件：
- EmbeddingModelComponent：Embedding 模型组件入口。

设计背景：统一多 provider 的嵌入模型接入方式，降低配置差异带来的出错率。
注意事项：部分 provider（如 IBM WatsonX、Ollama）需要额外字段或默认地址。
"""

from typing import Any

from lfx.base.embeddings.model import LCEmbeddingsModel
from lfx.base.models.unified_models import (
    get_api_key_for_provider,
    get_embedding_classes,
    get_embedding_model_options,
    update_model_options_in_build_config,
)
from lfx.base.models.watsonx_constants import IBM_WATSONX_URLS
from lfx.field_typing import Embeddings
from lfx.io import (
    BoolInput,
    DictInput,
    DropdownInput,
    FloatInput,
    IntInput,
    MessageTextInput,
    ModelInput,
    SecretStrInput,
)


class EmbeddingModelComponent(LCEmbeddingsModel):
    """Embedding 模型组件封装

    契约：依赖 `model` 选择结果；返回 `Embeddings` 实例或抛出 `ValueError`。
    关键路径：1) 校验模型选择 2) 解析元数据与参数映射 3) 构建嵌入类实例。
    决策：通过 `param_mapping` 动态组装参数而非硬编码
    问题：不同 provider 参数命名不一致
    方案：从模型元数据读取映射并统一构造 kwargs
    代价：元数据缺失会导致构建失败
    重评：当模型元数据稳定且可自动生成映射表时
    """

    display_name = "Embedding Model"
    description = "Generate embeddings using a specified provider."
    documentation: str = "https://docs.langflow.org/components-embedding-models"
    icon = "binary"
    name = "EmbeddingModel"
    category = "models"

    def update_build_config(self, build_config: dict, field_value: str, field_name: str | None = None):
        """更新构建配置与厂商特定字段显示

        契约：接收可变 `build_config` 并返回更新结果；副作用：调整字段 `show/required`。
        关键路径：1) 更新模型候选 2) 读取选中 provider 3) 切换 WatsonX 相关字段显示。
        异常流：不抛异常，未匹配 provider 时保持默认显示。
        决策：根据 provider 切换可见字段而非固定全部展示
        问题：不同厂商字段差异大，固定展示易误填
        方案：仅在选中 WatsonX 时展示相关字段
        代价：切换模型时字段会隐藏，旧值可能被忽略
        重评：当 UI 支持字段分组/显隐规则配置化时
        """
        # 实现：刷新模型选项列表。
        build_config = update_model_options_in_build_config(
            component=self,
            build_config=build_config,
            cache_key_prefix="embedding_model_options",
            get_options_func=get_embedding_model_options,
            field_name=field_name,
            field_value=field_value,
        )

        # 注意：根据 provider 切换特定字段显示状态。
        if field_name == "model" and isinstance(field_value, list) and len(field_value) > 0:
            selected_model = field_value[0]
            provider = selected_model.get("provider", "")

            # 注意：仅在 WatsonX 时展示相关字段。
            is_watsonx = provider == "IBM WatsonX"
            build_config["base_url_ibm_watsonx"]["show"] = is_watsonx
            build_config["project_id"]["show"] = is_watsonx
            build_config["truncate_input_tokens"]["show"] = is_watsonx
            build_config["input_text"]["show"] = is_watsonx
            if is_watsonx:
                build_config["base_url_ibm_watsonx"]["required"] = True
                build_config["project_id"]["required"] = True

        return build_config

    inputs = [
        ModelInput(
            name="model",
            display_name="Embedding Model",
            info="Select your model provider",
            real_time_refresh=True,
            required=True,
            model_type="embedding",
            input_types=["Embeddings"],  # 注意：覆盖默认类型，仅接受 Embeddings 输入。
        ),
        SecretStrInput(
            name="api_key",
            display_name="API Key",
            info="Model Provider API key",
            real_time_refresh=True,
            advanced=True,
        ),
        MessageTextInput(
            name="api_base",
            display_name="API Base URL",
            info="Base URL for the API. Leave empty for default.",
            advanced=True,
        ),
        # 注意：WatsonX 专用输入字段。
        DropdownInput(
            name="base_url_ibm_watsonx",
            display_name="watsonx API Endpoint",
            info="The base URL of the API (IBM watsonx.ai only)",
            options=IBM_WATSONX_URLS,
            value=IBM_WATSONX_URLS[0],
            show=False,
            real_time_refresh=True,
        ),
        MessageTextInput(
            name="project_id",
            display_name="Project ID",
            info="IBM watsonx.ai Project ID (required for IBM watsonx.ai)",
            show=False,
        ),
        IntInput(
            name="dimensions",
            display_name="Dimensions",
            info="The number of dimensions the resulting output embeddings should have. "
            "Only supported by certain models.",
            advanced=True,
        ),
        IntInput(
            name="chunk_size",
            display_name="Chunk Size",
            advanced=True,
            value=1000,
        ),
        FloatInput(
            name="request_timeout",
            display_name="Request Timeout",
            advanced=True,
        ),
        IntInput(
            name="max_retries",
            display_name="Max Retries",
            advanced=True,
            value=3,
        ),
        BoolInput(
            name="show_progress_bar",
            display_name="Show Progress Bar",
            advanced=True,
        ),
        DictInput(
            name="model_kwargs",
            display_name="Model Kwargs",
            advanced=True,
            info="Additional keyword arguments to pass to the model.",
        ),
        IntInput(
            name="truncate_input_tokens",
            display_name="Truncate Input Tokens",
            advanced=True,
            value=200,
            show=False,
        ),
        BoolInput(
            name="input_text",
            display_name="Include the original text in the output",
            value=True,
            advanced=True,
            show=False,
        ),
    ]

    def build_embeddings(self) -> Embeddings:
        """构建 Embeddings 实例

        契约：`self.model` 必须为非空 list，成功返回 Embeddings 实例。
        关键路径：1) 接收直接传入的 Embeddings 2) 校验模型/元数据 3) 组装 kwargs 并实例化。
        异常流：缺少 API Key / 模型名 / embedding_class 时抛 `ValueError`。
        决策：允许直接传入 Embeddings 实例以复用外部构建
        问题：部分流程已在外部构建完成
        方案：优先返回 `BaseEmbeddings` 实例
        代价：需要运行时类型检查并容忍 ImportError
        重评：当组件统一走配置构建且不再支持直连实例时
        """
        # 注意：直接传入 Embeddings 时不再二次构建。
        try:
            from langchain_core.embeddings import Embeddings as BaseEmbeddings

            if isinstance(self.model, BaseEmbeddings):
                return self.model
        except ImportError:
            pass

        # 实现：安全读取模型配置字段。
        if not self.model or not isinstance(self.model, list):
            msg = "Model must be a non-empty list"
            raise ValueError(msg)

        model = self.model[0]
        model_name = model.get("name")
        provider = model.get("provider")
        metadata = model.get("metadata", {})

        # 实现：优先使用组件输入，其次读取全局 API Key。
        api_key = get_api_key_for_provider(self.user_id, provider, self.api_key)

        # 注意：Ollama 允许无 API Key，其余 provider 需校验。
        if not api_key and provider != "Ollama":
            msg = (
                f"{provider} API key is required. "
                f"Please provide it in the component or configure it globally as "
                f"{provider.upper().replace(' ', '_')}_API_KEY."
            )
            raise ValueError(msg)

        if not model_name:
            msg = "Model name is required"
            raise ValueError(msg)

        # 实现：根据元数据定位 embedding 类。
        embedding_class_name = metadata.get("embedding_class")
        if not embedding_class_name:
            msg = f"No embedding class defined in metadata for {model_name}"
            raise ValueError(msg)

        embedding_class = get_embedding_classes().get(embedding_class_name)
        if not embedding_class:
            msg = f"Unknown embedding class: {embedding_class_name}"
            raise ValueError(msg)

        # 实现：使用参数映射构建初始化参数。
        kwargs = self._build_kwargs(model, metadata)

        return embedding_class(**kwargs)

    def _build_kwargs(self, model: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
        """根据参数映射构建 kwargs

        契约：`metadata` 必须包含 `param_mapping`；返回供 embedding_class 使用的参数字典。
        关键路径：1) 处理必填参数映射 2) 补齐可选参数 3) 处理厂商特殊参数。
        异常流：缺少映射时抛 `ValueError`。
        决策：对厂商差异做最小化分支处理
        问题：WatsonX/Ollama 等需要特殊字段
        方案：在通用映射外追加 provider 特殊字段
        代价：新增 provider 需扩展分支
        重评：当 provider 配置可元数据化描述时
        """
        param_mapping = metadata.get("param_mapping", {})
        if not param_mapping:
            msg = "Parameter mapping not found in metadata"
            raise ValueError(msg)

        kwargs = {}

        # 注意：必填参数支持 "model"/"model_id" 两种映射。
        if "model" in param_mapping:
            kwargs[param_mapping["model"]] = model.get("name")
        elif "model_id" in param_mapping:
            kwargs[param_mapping["model_id"]] = model.get("name")
        if "api_key" in param_mapping:
            kwargs[param_mapping["api_key"]] = get_api_key_for_provider(
                self.user_id,
                model.get("provider"),
                self.api_key,
            )

        # 实现：整理可选参数。
        provider = model.get("provider")
        optional_params = {
            "api_base": self.api_base if self.api_base else None,
            "dimensions": int(self.dimensions) if self.dimensions else None,
            "chunk_size": int(self.chunk_size) if self.chunk_size else None,
            "request_timeout": float(self.request_timeout) if self.request_timeout else None,
            "max_retries": int(self.max_retries) if self.max_retries else None,
            "show_progress_bar": self.show_progress_bar if hasattr(self, "show_progress_bar") else None,
            "model_kwargs": self.model_kwargs if self.model_kwargs else None,
        }

        # 注意：WatsonX 专用参数映射。
        if provider in {"IBM WatsonX", "IBM watsonx.ai"}:
            # 注意：watsonx 的 base_url 映射为 "url" 参数。
            if "url" in param_mapping:
                url_value = (
                    self.base_url_ibm_watsonx
                    if hasattr(self, "base_url_ibm_watsonx") and self.base_url_ibm_watsonx
                    else "https://us-south.ml.cloud.ibm.com"
                )
                kwargs[param_mapping["url"]] = url_value
            # 注意：watsonx 的 project_id 需要单独映射。
            if hasattr(self, "project_id") and self.project_id and "project_id" in param_mapping:
                kwargs[param_mapping["project_id"]] = self.project_id

        # 注意：Ollama 专用参数映射。
        if provider == "Ollama" and "base_url" in param_mapping:
            # 注意：Ollama 将 api_base 映射为 base_url。
            base_url_value = self.api_base if hasattr(self, "api_base") and self.api_base else "http://localhost:11434"
            kwargs[param_mapping["base_url"]] = base_url_value

        # 注意：仅在参数有值且存在映射时写入 kwargs。
        for param_name, param_value in optional_params.items():
            if param_value is not None and param_name in param_mapping:
                # 注意：Google provider 需要特殊 timeout 结构。
                if param_name == "request_timeout":
                    if provider == "Google" and isinstance(param_value, (int, float)):
                        # 注意：Google SDK 期望 `{"timeout": seconds}` 结构而非纯数值。
                        kwargs[param_mapping[param_name]] = {"timeout": param_value}
                    else:
                        kwargs[param_mapping[param_name]] = param_value
                else:
                    kwargs[param_mapping[param_name]] = param_value

        return kwargs
