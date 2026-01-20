"""
模块名称：模型输入配置与提供方映射

本模块负责构建模型提供方的输入字段与 UI 配置，主要用于动态生成可用模型的表单配置。
主要功能包括：
- 过滤组件输入以生成提供方专属字段
- 按输入类型与名称调整 UI 行为
- 在模块加载时尝试注册可用的模型提供方

关键组件：
- `MODEL_PROVIDERS_DICT` / `ACTIVE_MODEL_PROVIDERS_DICT`
- `get_filtered_inputs` / `process_inputs`

设计背景：不同模型提供方的输入字段差异大，需要统一的构建与过滤入口。
注意事项：模块导入阶段会尝试导入可选依赖，失败会被忽略。
"""

from typing_extensions import TypedDict

from lfx.base.models.model import LCModelComponent
from lfx.inputs.inputs import InputTypes, SecretStrInput
from lfx.template.field.base import Input


class ModelProvidersDict(TypedDict):
    """模型提供方配置字典结构。"""

    fields: dict
    inputs: list[InputTypes]
    prefix: str
    component_class: LCModelComponent
    icon: str
    is_active: bool


def get_filtered_inputs(component_class, provider_name: str | None = None):
    """获取并过滤掉通用输入后的组件输入列表。

    契约：返回仅包含提供方专属字段的输入配置列表。
    副作用：实例化一次组件以读取其 `inputs`。
    失败语义：组件初始化异常由调用方处理。
    """
    base_input_names = {field.name for field in LCModelComponent.get_base_inputs()}
    component_instance = component_class()

    return [
        process_inputs(input_, provider_name)
        for input_ in component_instance.inputs
        if input_.name not in base_input_names
    ]


def process_inputs(component_data: Input, provider_name: str | None = None):
    """按输入类型/名称调整 UI 行为与默认值。

    契约：返回修改后的输入配置对象。
    关键路径（三步）：
    1) 处理敏感输入（如 API Key）并关闭默认值回填
    2) 调整特定字段为高级/实时刷新/下拉框
    3) 对模型名字段追加引导说明
    异常流：类型不匹配不抛错，按默认路径返回。
    性能瓶颈：无，纯内存操作。
    排障入口：检查字段 `advanced/real_time_refresh/combobox` 是否按预期设置。
    决策：敏感字段默认不从数据库加载
    问题：自动回填 API key 会带来泄露风险
    方案：清空 `SecretStrInput.value` 并关闭 `load_from_db`
    代价：用户需重新输入密钥
    重评：当提供安全的凭据托管与脱敏展示时可恢复回填
    """
    if isinstance(component_data, SecretStrInput):
        component_data.value = ""
        component_data.load_from_db = False
        component_data.real_time_refresh = True
        if component_data.name == "api_key":
            component_data.required = False
    elif component_data.name == "tool_model_enabled":
        component_data.advanced = True
        component_data.value = True
    elif component_data.name in {"temperature", "base_url"}:
        if provider_name not in ["IBM watsonx.ai", "Ollama"]:
            component_data = set_advanced_true(component_data)
    elif component_data.name == "model_name":
        if provider_name not in ["IBM watsonx.ai"]:
            component_data = set_real_time_refresh_false(component_data)
        component_data = add_combobox_true(component_data)
        component_data = add_info(
            component_data,
            "To see the model names, first choose a provider. Then, enter your API key and click the refresh button "
            "next to the model name.",
        )
    return component_data


def set_advanced_true(component_input):
    """将输入标记为高级选项。"""
    component_input.advanced = True
    return component_input


def set_real_time_refresh_false(component_input):
    """关闭输入的实时刷新行为。"""
    component_input.real_time_refresh = False
    return component_input


def add_info(component_input, info_str: str):
    """为输入追加 UI 提示信息。"""
    component_input.info = info_str
    return component_input


def add_combobox_true(component_input):
    """将输入标记为可选下拉框。"""
    component_input.combobox = True
    return component_input


def create_input_fields_dict(inputs: list[Input], prefix: str) -> dict[str, Input]:
    """将输入列表转换为带前缀的字段字典。"""
    return {f"{prefix}{input_.name}": input_.to_dict() for input_ in inputs}


def _get_ollama_inputs_and_fields():
    """获取 Ollama 组件输入与字段字典。"""
    try:
        from lfx.components.ollama.ollama import ChatOllamaComponent

        ollama_inputs = get_filtered_inputs(ChatOllamaComponent, provider_name="Ollama")
    except ImportError as e:
        msg = "Ollama is not installed. Please install it with `pip install langchain-ollama`."
        raise ImportError(msg) from e
    return ollama_inputs, create_input_fields_dict(ollama_inputs, "")


def _get_watsonx_inputs_and_fields():
    """获取 IBM WatsonX 组件输入与字段字典。"""
    try:
        from lfx.components.ibm.watsonx import WatsonxAIComponent

        watsonx_inputs = get_filtered_inputs(WatsonxAIComponent, provider_name="IBM watsonx.ai")
    except ImportError as e:
        msg = "IBM watsonx.ai is not installed. Please install it with `pip install langchain-ibm-watsonx`."
        raise ImportError(msg) from e
    return watsonx_inputs, create_input_fields_dict(watsonx_inputs, "")


def _get_google_generative_ai_inputs_and_fields():
    """获取 Google Generative AI 组件输入与字段字典。"""
    try:
        from lfx.components.google.google_generative_ai import GoogleGenerativeAIComponent

        google_generative_ai_inputs = get_filtered_inputs(GoogleGenerativeAIComponent)
    except ImportError as e:
        msg = (
            "Google Generative AI is not installed. Please install it with "
            "`pip install langchain-google-generative-ai`."
        )
        raise ImportError(msg) from e
    return google_generative_ai_inputs, create_input_fields_dict(google_generative_ai_inputs, "")


def _get_openai_inputs_and_fields():
    """获取 OpenAI 组件输入与字段字典。"""
    try:
        from lfx.components.openai.openai_chat_model import OpenAIModelComponent

        openai_inputs = get_filtered_inputs(OpenAIModelComponent)
    except ImportError as e:
        msg = "OpenAI is not installed. Please install it with `pip install langchain-openai`."
        raise ImportError(msg) from e
    return openai_inputs, create_input_fields_dict(openai_inputs, "")


def _get_azure_inputs_and_fields():
    """获取 Azure OpenAI 组件输入与字段字典。"""
    try:
        from lfx.components.azure.azure_openai import AzureChatOpenAIComponent

        azure_inputs = get_filtered_inputs(AzureChatOpenAIComponent)
    except ImportError as e:
        msg = "Azure OpenAI is not installed. Please install it with `pip install langchain-azure-openai`."
        raise ImportError(msg) from e
    return azure_inputs, create_input_fields_dict(azure_inputs, "")


def _get_groq_inputs_and_fields():
    """获取 Groq 组件输入与字段字典。"""
    try:
        from lfx.components.groq.groq import GroqModel

        groq_inputs = get_filtered_inputs(GroqModel)
    except ImportError as e:
        msg = "Groq is not installed. Please install it with `pip install langchain-groq`."
        raise ImportError(msg) from e
    return groq_inputs, create_input_fields_dict(groq_inputs, "")


def _get_anthropic_inputs_and_fields():
    """获取 Anthropic 组件输入与字段字典。"""
    try:
        from lfx.components.anthropic.anthropic import AnthropicModelComponent

        anthropic_inputs = get_filtered_inputs(AnthropicModelComponent)
    except ImportError as e:
        msg = "Anthropic is not installed. Please install it with `pip install langchain-anthropic`."
        raise ImportError(msg) from e
    return anthropic_inputs, create_input_fields_dict(anthropic_inputs, "")


def _get_nvidia_inputs_and_fields():
    """获取 NVIDIA 组件输入与字段字典。"""
    try:
        from lfx.components.nvidia.nvidia import NVIDIAModelComponent

        nvidia_inputs = get_filtered_inputs(NVIDIAModelComponent)
    except ImportError as e:
        msg = "NVIDIA is not installed. Please install it with `pip install langchain-nvidia`."
        raise ImportError(msg) from e
    return nvidia_inputs, create_input_fields_dict(nvidia_inputs, "")


def _get_amazon_bedrock_inputs_and_fields():
    """获取 Amazon Bedrock 组件输入与字段字典。"""
    try:
        from lfx.components.amazon.amazon_bedrock_model import AmazonBedrockComponent

        amazon_bedrock_inputs = get_filtered_inputs(AmazonBedrockComponent)
    except ImportError as e:
        msg = "Amazon Bedrock is not installed. Please install it with `pip install langchain-amazon-bedrock`."
        raise ImportError(msg) from e
    return amazon_bedrock_inputs, create_input_fields_dict(amazon_bedrock_inputs, "")


def _get_sambanova_inputs_and_fields():
    """获取 SambaNova 组件输入与字段字典。"""
    try:
        from lfx.components.sambanova.sambanova import SambaNovaComponent

        sambanova_inputs = get_filtered_inputs(SambaNovaComponent)
    except ImportError as e:
        msg = "SambaNova is not installed. Please install it with `pip install langchain-sambanova`."
        raise ImportError(msg) from e
    return sambanova_inputs, create_input_fields_dict(sambanova_inputs, "")


MODEL_PROVIDERS_DICT: dict[str, ModelProvidersDict] = {}

# 逐个尝试注册可用提供方（可选依赖缺失会被忽略）
try:
    from lfx.components.openai.openai_chat_model import OpenAIModelComponent

    openai_inputs, openai_fields = _get_openai_inputs_and_fields()
    MODEL_PROVIDERS_DICT["OpenAI"] = {
        "fields": openai_fields,
        "inputs": openai_inputs,
        "prefix": "",
        "component_class": OpenAIModelComponent(),
        "icon": OpenAIModelComponent.icon,
        "is_active": True,
    }
except ImportError:
    pass

try:
    from lfx.components.azure.azure_openai import AzureChatOpenAIComponent

    azure_inputs, azure_fields = _get_azure_inputs_and_fields()
    MODEL_PROVIDERS_DICT["Azure OpenAI"] = {
        "fields": azure_fields,
        "inputs": azure_inputs,
        "prefix": "",
        "component_class": AzureChatOpenAIComponent(),
        "icon": AzureChatOpenAIComponent.icon,
        "is_active": False,
    }
except ImportError:
    pass

try:
    from lfx.components.groq.groq import GroqModel

    groq_inputs, groq_fields = _get_groq_inputs_and_fields()
    MODEL_PROVIDERS_DICT["Groq"] = {
        "fields": groq_fields,
        "inputs": groq_inputs,
        "prefix": "",
        "component_class": GroqModel(),
        "icon": GroqModel.icon,
        "is_active": False,
    }
except ImportError:
    pass

try:
    from lfx.components.anthropic.anthropic import AnthropicModelComponent

    anthropic_inputs, anthropic_fields = _get_anthropic_inputs_and_fields()
    MODEL_PROVIDERS_DICT["Anthropic"] = {
        "fields": anthropic_fields,
        "inputs": anthropic_inputs,
        "prefix": "",
        "component_class": AnthropicModelComponent(),
        "icon": AnthropicModelComponent.icon,
        "is_active": True,
    }
except ImportError:
    pass

try:
    from lfx.components.nvidia.nvidia import NVIDIAModelComponent

    nvidia_inputs, nvidia_fields = _get_nvidia_inputs_and_fields()
    MODEL_PROVIDERS_DICT["NVIDIA"] = {
        "fields": nvidia_fields,
        "inputs": nvidia_inputs,
        "prefix": "",
        "component_class": NVIDIAModelComponent(),
        "icon": NVIDIAModelComponent.icon,
        "is_active": False,
    }
except ImportError:
    pass

try:
    from lfx.components.amazon.amazon_bedrock_model import AmazonBedrockComponent

    bedrock_inputs, bedrock_fields = _get_amazon_bedrock_inputs_and_fields()
    MODEL_PROVIDERS_DICT["Amazon Bedrock"] = {
        "fields": bedrock_fields,
        "inputs": bedrock_inputs,
        "prefix": "",
        "component_class": AmazonBedrockComponent(),
        "icon": AmazonBedrockComponent.icon,
        "is_active": False,
    }
except ImportError:
    pass

try:
    from lfx.components.google.google_generative_ai import GoogleGenerativeAIComponent

    google_generative_ai_inputs, google_generative_ai_fields = _get_google_generative_ai_inputs_and_fields()
    MODEL_PROVIDERS_DICT["Google Generative AI"] = {
        "fields": google_generative_ai_fields,
        "inputs": google_generative_ai_inputs,
        "prefix": "",
        "component_class": GoogleGenerativeAIComponent(),
        "icon": GoogleGenerativeAIComponent.icon,
        "is_active": True,
    }
except ImportError:
    pass

try:
    from lfx.components.sambanova.sambanova import SambaNovaComponent

    sambanova_inputs, sambanova_fields = _get_sambanova_inputs_and_fields()
    MODEL_PROVIDERS_DICT["SambaNova"] = {
        "fields": sambanova_fields,
        "inputs": sambanova_inputs,
        "prefix": "",
        "component_class": SambaNovaComponent(),
        "icon": SambaNovaComponent.icon,
        "is_active": False,
    }
except ImportError:
    pass

try:
    from lfx.components.ibm.watsonx import WatsonxAIComponent

    watsonx_inputs, watsonx_fields = _get_watsonx_inputs_and_fields()
    MODEL_PROVIDERS_DICT["IBM watsonx.ai"] = {
        "fields": watsonx_fields,
        "inputs": watsonx_inputs,
        "prefix": "",
        "component_class": WatsonxAIComponent(),
        "icon": WatsonxAIComponent.icon,
        "is_active": True,
    }
except ImportError:
    pass

try:
    from lfx.components.ollama.ollama import ChatOllamaComponent

    ollama_inputs, ollama_fields = _get_ollama_inputs_and_fields()
    MODEL_PROVIDERS_DICT["Ollama"] = {
        "fields": ollama_fields,
        "inputs": ollama_inputs,
        "prefix": "",
        "component_class": ChatOllamaComponent(),
        "icon": ChatOllamaComponent.icon,
        "is_active": True,
    }
except ImportError:
    pass

# 仅暴露激活的提供方 ----------------------------------------------
ACTIVE_MODEL_PROVIDERS_DICT: dict[str, ModelProvidersDict] = {
    name: prov for name, prov in MODEL_PROVIDERS_DICT.items() if prov.get("is_active", True)
}

MODEL_PROVIDERS: list[str] = list(ACTIVE_MODEL_PROVIDERS_DICT.keys())

ALL_PROVIDER_FIELDS: list[str] = [field for prov in ACTIVE_MODEL_PROVIDERS_DICT.values() for field in prov["fields"]]

MODEL_DYNAMIC_UPDATE_FIELDS = [
    "api_key",
    "model",
    "tool_model_enabled",
    "base_url",
    "model_name",
    "watsonx_endpoint",
    "url",
]

MODELS_METADATA = {name: {"icon": prov["icon"]} for name, prov in ACTIVE_MODEL_PROVIDERS_DICT.items()}

MODEL_PROVIDERS_LIST = ["Anthropic", "Google Generative AI", "OpenAI", "IBM watsonx.ai", "Ollama"]

MODEL_OPTIONS_METADATA = [MODELS_METADATA[key] for key in MODEL_PROVIDERS_LIST if key in MODELS_METADATA]
