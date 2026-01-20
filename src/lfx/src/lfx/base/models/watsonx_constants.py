"""
模块名称：IBM WatsonX 模型元数据

本模块维护 WatsonX 的默认 LLM 与嵌入模型列表以及常用服务地址，主要用于 UI 选择与默认配置。
主要功能包括：
- 提供默认 LLM/Embedding 模型元数据
- 生成组合列表与模型名称列表
- 提供常用 API Endpoint 列表

关键组件：
- `WATSONX_MODELS_DETAILED`：LLM 与 Embedding 合并元数据
- `IBM_WATSONX_URLS`：常用服务地址

设计背景：WatsonX 配置项较多，提供默认模型与地址以降低配置成本。
注意事项：默认列表仅用于引导，实际可用性以云端权限为准。
"""

from .model_metadata import create_model_metadata

WATSONX_DEFAULT_LLM_MODELS = [
    create_model_metadata(
        provider="IBM WatsonX",
        name="ibm/granite-3-2b-instruct",
        icon="IBM",
        model_type="llm",
        tool_calling=True,
        default=True,
    ),
    create_model_metadata(
        provider="IBM WatsonX",
        name="ibm/granite-3-8b-instruct",
        icon="IBM",
        model_type="llm",
        tool_calling=True,
        default=True,
    ),
    create_model_metadata(
        provider="IBM WatsonX",
        name="ibm/granite-13b-instruct-v2",
        icon="IBM",
        model_type="llm",
        tool_calling=True,
        default=True,
    ),
]

WATSONX_DEFAULT_EMBEDDING_MODELS = [
    create_model_metadata(
        provider="IBM WatsonX",
        name="sentence-transformers/all-minilm-l12-v2",
        icon="IBM",
        model_type="embeddings",
        tool_calling=True,
        default=True,
    ),
    create_model_metadata(
        provider="IBM WatsonX",
        name="ibm/slate-125m-english-rtrvr-v2",
        icon="IBM",
        model_type="embeddings",
        tool_calling=True,
        default=True,
    ),
    create_model_metadata(
        provider="IBM WatsonX",
        name="ibm/slate-30m-english-rtrvr-v2",
        icon="IBM",
        model_type="embeddings",
        tool_calling=True,
        default=True,
    ),
    create_model_metadata(
        provider="IBM WatsonX",
        name="intfloat/multilingual-e5-large",
        icon="IBM",
        model_type="embeddings",
        tool_calling=True,
        default=True,
    ),
]


WATSONX_EMBEDDING_MODELS_DETAILED = WATSONX_DEFAULT_EMBEDDING_MODELS
# 合并后的模型元数据列表
WATSONX_MODELS_DETAILED = WATSONX_DEFAULT_LLM_MODELS + WATSONX_DEFAULT_EMBEDDING_MODELS

WATSONX_EMBEDDING_MODEL_NAMES = [metadata["name"] for metadata in WATSONX_DEFAULT_EMBEDDING_MODELS]

# 常用服务区域地址
IBM_WATSONX_URLS = [
    "https://us-south.ml.cloud.ibm.com",
    "https://eu-de.ml.cloud.ibm.com",
    "https://eu-gb.ml.cloud.ibm.com",
    "https://au-syd.ml.cloud.ibm.com",
    "https://jp-tok.ml.cloud.ibm.com",
    "https://ca-tor.ml.cloud.ibm.com",
]
