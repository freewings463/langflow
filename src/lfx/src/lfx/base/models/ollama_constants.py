"""
模块名称：Ollama 模型元数据

本模块维护 Ollama 的模型元数据、嵌入模型与常见连接地址，主要用于 UI 选择与兼容支持。
主要功能包括：
- 提供支持工具调用的模型元数据列表
- 提供嵌入模型的简表与元数据表
- 提供常用本地连接地址与默认 API URL

关键组件：
- `OLLAMA_MODELS_DETAILED`：工具调用模型元数据
- `OLLAMA_EMBEDDING_MODELS`：嵌入模型名列表

设计背景：Ollama 模型与部署地址多样，需提供稳定的默认项。
注意事项：此列表不等同于本地实际安装模型列表。
"""

from .model_metadata import create_model_metadata

# 统一元数据：作为唯一事实来源
OLLAMA_MODELS_DETAILED = [
    # 支持工具调用的模型
    create_model_metadata(
        provider="Ollama",
        name="llama3.3",
        icon="Ollama",
        tool_calling=True,
    ),
    create_model_metadata(
        provider="Ollama",
        name="qwq",
        icon="Ollama",
        tool_calling=True,
    ),
    create_model_metadata(
        provider="Ollama",
        name="llama3.2",
        icon="Ollama",
        tool_calling=True,
    ),
    create_model_metadata(
        provider="Ollama",
        name="llama3.1",
        icon="Ollama",
        tool_calling=True,
    ),
    create_model_metadata(
        provider="Ollama",
        name="mistral",
        icon="Ollama",
        tool_calling=True,
    ),
    create_model_metadata(
        provider="Ollama",
        name="qwen2",
        icon="Ollama",
        tool_calling=True,
    ),
    create_model_metadata(
        provider="Ollama",
        name="qwen2.5",
        icon="Ollama",
        tool_calling=True,
    ),
    create_model_metadata(
        provider="Ollama",
        name="qwen2.5-coder",
        icon="Ollama",
        tool_calling=True,
    ),
    create_model_metadata(
        provider="Ollama",
        name="mistral-nemo",
        icon="Ollama",
        tool_calling=True,
    ),
    create_model_metadata(
        provider="Ollama",
        name="mixtral",
        icon="Ollama",
        tool_calling=True,
    ),
    create_model_metadata(
        provider="Ollama",
        name="command-r",
        icon="Ollama",
        tool_calling=True,
    ),
    create_model_metadata(
        provider="Ollama",
        name="command-r-plus",
        icon="Ollama",
        tool_calling=True,
    ),
    create_model_metadata(
        provider="Ollama",
        name="mistral-large",
        icon="Ollama",
        tool_calling=True,
    ),
    create_model_metadata(
        provider="Ollama",
        name="smollm2",
        icon="Ollama",
        tool_calling=True,
    ),
    create_model_metadata(
        provider="Ollama",
        name="hermes3",
        icon="Ollama",
        tool_calling=True,
    ),
    create_model_metadata(
        provider="Ollama",
        name="athene-v2",
        icon="Ollama",
        tool_calling=True,
    ),
    create_model_metadata(
        provider="Ollama",
        name="mistral-small",
        icon="Ollama",
        tool_calling=True,
    ),
    create_model_metadata(
        provider="Ollama",
        name="nemotron-mini",
        icon="Ollama",
        tool_calling=True,
    ),
    create_model_metadata(
        provider="Ollama",
        name="nemotron",
        icon="Ollama",
        tool_calling=True,
    ),
    create_model_metadata(
        provider="Ollama",
        name="llama3-groq-tool-use",
        icon="Ollama",
        tool_calling=True,
    ),
    create_model_metadata(
        provider="Ollama",
        name="granite3-dense",
        icon="Ollama",
        tool_calling=True,
    ),
    create_model_metadata(
        provider="Ollama",
        name="granite3.1-dense",
        icon="Ollama",
        tool_calling=True,
    ),
    create_model_metadata(
        provider="Ollama",
        name="aya-expanse",
        icon="Ollama",
        tool_calling=True,
    ),
    create_model_metadata(
        provider="Ollama",
        name="granite3-moe",
        icon="Ollama",
        tool_calling=True,
    ),
    create_model_metadata(
        provider="Ollama",
        name="firefunction-v2",
        icon="Ollama",
        tool_calling=True,
    ),
    create_model_metadata(
        provider="Ollama",
        name="cogito",
        icon="Ollama",
        tool_calling=True,
    ),
    create_model_metadata(
        provider="Ollama",
        name="gpt-oss:20b",
        icon="Ollama",
        tool_calling=True,
    ),
    create_model_metadata(
        provider="Ollama",
        name="qwen3-vl:4b",
        icon="Ollama",
        tool_calling=True,
    ),
]

# 依据元数据过滤得到的可用列表
OLLAMA_TOOL_MODELS_BASE = [
    metadata["name"]
    for metadata in OLLAMA_MODELS_DETAILED
    if metadata.get("tool_calling", False) and not metadata.get("not_supported", False)
]

# 嵌入模型列表（遵循 OpenAI 简表风格）
# https://ollama.com/search?c=embedding
OLLAMA_EMBEDDING_MODELS = [
    "nomic-embed-text",
    "mxbai-embed-large",
    "snowflake-arctic-embed",
    "all-minilm",
    "bge-m3",
    "bge-large",
    "paraphrase-multilingual",
    "granite-embedding",
    "jina-embeddings-v2-base-en",
]

# 嵌入模型元数据版本
OLLAMA_EMBEDDING_MODELS_DETAILED = [
    create_model_metadata(
        provider="Ollama",
        name=name,
        icon="Ollama",
        model_type="embeddings",
    )
    for name in OLLAMA_EMBEDDING_MODELS
]

# 常见连接地址
URL_LIST = [
    "http://localhost:11434",
    "http://host.docker.internal:11434",
    "http://127.0.0.1:11434",
    "http://0.0.0.0:11434",
]

# 向后兼容别名
OLLAMA_MODEL_NAMES = OLLAMA_TOOL_MODELS_BASE
DEFAULT_OLLAMA_API_URL = "https://ollama.com"
