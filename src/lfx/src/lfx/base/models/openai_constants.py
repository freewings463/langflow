"""
模块名称：OpenAI 模型元数据

本模块维护 OpenAI 的模型元数据与衍生列表，主要用于统一模型注册与 UI 选择过滤。
主要功能包括：
- 提供包含推理/搜索/不支持标记的模型元数据
- 生成聊天、推理、搜索与嵌入模型列表

关键组件：
- `OPENAI_MODELS_DETAILED`：元数据主列表
- `OPENAI_CHAT_MODEL_NAMES`：可用聊天模型列表

设计背景：统一模型来源，便于按能力与状态过滤。
注意事项：`not_supported`/`preview` 模型默认不参与可用列表。
"""

from .model_metadata import create_model_metadata

# 统一元数据：作为唯一事实来源
OPENAI_MODELS_DETAILED = [
    # GPT-5 系列
    create_model_metadata(
        provider="OpenAI",
        name="gpt-5.1",
        icon="OpenAI",
        tool_calling=True,
        reasoning=True,
    ),
    create_model_metadata(
        provider="OpenAI",
        name="gpt-5",
        icon="OpenAI",
        tool_calling=True,
        reasoning=True,
    ),
    create_model_metadata(
        provider="OpenAI",
        name="gpt-5-mini",
        icon="OpenAI",
        tool_calling=True,
        reasoning=True,
    ),
    create_model_metadata(
        provider="OpenAI",
        name="gpt-5-nano",
        icon="OpenAI",
        tool_calling=True,
        reasoning=True,
    ),
    create_model_metadata(
        provider="OpenAI",
        name="gpt-5-chat-latest",
        icon="OpenAI",
        tool_calling=False,
        reasoning=True,
    ),
    # 常规 OpenAI 模型
    create_model_metadata(provider="OpenAI", name="gpt-4o-mini", icon="OpenAI", tool_calling=True),
    create_model_metadata(provider="OpenAI", name="gpt-4o", icon="OpenAI", tool_calling=True),
    create_model_metadata(
        provider="OpenAI", name="gpt-4.1", icon="OpenAI", tool_calling=True, preview=True, not_supported=True
    ),
    create_model_metadata(
        provider="OpenAI", name="gpt-4.1-mini", icon="OpenAI", tool_calling=True, preview=True, not_supported=True
    ),
    create_model_metadata(
        provider="OpenAI", name="gpt-4.1-nano", icon="OpenAI", tool_calling=True, preview=True, not_supported=True
    ),
    create_model_metadata(
        provider="OpenAI", name="gpt-4.5-preview", icon="OpenAI", tool_calling=True, preview=True, not_supported=True
    ),
    create_model_metadata(provider="OpenAI", name="gpt-4-turbo", icon="OpenAI", tool_calling=True),
    create_model_metadata(
        provider="OpenAI", name="gpt-4-turbo-preview", icon="OpenAI", tool_calling=True, preview=True, deprecated=True
    ),
    create_model_metadata(provider="OpenAI", name="gpt-4", icon="OpenAI", tool_calling=True),
    create_model_metadata(provider="OpenAI", name="gpt-3.5-turbo", icon="OpenAI", tool_calling=True, deprecated=True),
    # 推理模型
    create_model_metadata(provider="OpenAI", name="o1", icon="OpenAI", reasoning=True),
    create_model_metadata(provider="OpenAI", name="o1-mini", icon="OpenAI", reasoning=True, not_supported=True),
    create_model_metadata(provider="OpenAI", name="o1-pro", icon="OpenAI", reasoning=True, not_supported=True),
    create_model_metadata(
        provider="OpenAI", name="o3-mini", icon="OpenAI", reasoning=True, preview=True, not_supported=True
    ),
    create_model_metadata(
        provider="OpenAI", name="o3", icon="OpenAI", reasoning=True, preview=True, not_supported=True
    ),
    create_model_metadata(
        provider="OpenAI", name="o3-pro", icon="OpenAI", reasoning=True, preview=True, not_supported=True
    ),
    create_model_metadata(
        provider="OpenAI", name="o4-mini", icon="OpenAI", reasoning=True, preview=True, not_supported=True
    ),
    create_model_metadata(
        provider="OpenAI", name="o4-mini-high", icon="OpenAI", reasoning=True, preview=True, not_supported=True
    ),
    # 搜索模型
    create_model_metadata(
        provider="OpenAI",
        name="gpt-4o-mini-search-preview",
        icon="OpenAI",
        tool_calling=True,
        search=True,
        preview=True,
    ),
    create_model_metadata(
        provider="OpenAI",
        name="gpt-4o-search-preview",
        icon="OpenAI",
        tool_calling=True,
        search=True,
        preview=True,
    ),
    # 不支持模型
    create_model_metadata(
        provider="OpenAI", name="computer-use-preview", icon="OpenAI", not_supported=True, preview=True
    ),
    create_model_metadata(
        provider="OpenAI", name="gpt-4o-audio-preview", icon="OpenAI", not_supported=True, preview=True
    ),
    create_model_metadata(
        provider="OpenAI", name="gpt-4o-realtime-preview", icon="OpenAI", not_supported=True, preview=True
    ),
    create_model_metadata(
        provider="OpenAI", name="gpt-4o-mini-audio-preview", icon="OpenAI", not_supported=True, preview=True
    ),
    create_model_metadata(
        provider="OpenAI", name="gpt-4o-mini-realtime-preview", icon="OpenAI", not_supported=True, preview=True
    ),
]
# 可用聊天模型：排除 not_supported / reasoning / search
OPENAI_CHAT_MODEL_NAMES = [
    metadata["name"]
    for metadata in OPENAI_MODELS_DETAILED
    if not metadata.get("not_supported", False)
    and not metadata.get("reasoning", False)
    and not metadata.get("search", False)
]

# 可用推理模型：排除 not_supported
OPENAI_REASONING_MODEL_NAMES = [
    metadata["name"]
    for metadata in OPENAI_MODELS_DETAILED
    if metadata.get("reasoning", False) and not metadata.get("not_supported", False)
]

# 可用搜索模型：排除 not_supported
OPENAI_SEARCH_MODEL_NAMES = [
    metadata["name"]
    for metadata in OPENAI_MODELS_DETAILED
    if metadata.get("search", False) and not metadata.get("not_supported", False)
]

# 不支持模型列表（用于 UI 提示）
NOT_SUPPORTED_MODELS = [metadata["name"] for metadata in OPENAI_MODELS_DETAILED if metadata.get("not_supported", False)]

# 嵌入模型名称
OPENAI_EMBEDDING_MODEL_NAMES = [
    "text-embedding-3-small",
    "text-embedding-3-large",
    "text-embedding-ada-002",
]

# 嵌入模型元数据版本
OPENAI_EMBEDDING_MODELS_DETAILED = [
    create_model_metadata(
        provider="OpenAI",
        name=name,
        icon="OpenAI",
        model_type="embeddings",
    )
    for name in OPENAI_EMBEDDING_MODEL_NAMES
]

# 向后兼容别名
MODEL_NAMES = OPENAI_CHAT_MODEL_NAMES
OPENAI_MODEL_NAMES = OPENAI_CHAT_MODEL_NAMES
