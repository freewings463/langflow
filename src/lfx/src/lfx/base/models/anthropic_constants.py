"""
模块名称：Anthropic 模型元数据

本模块维护 Anthropic 相关模型的静态元数据列表，主要用于在统一模型注册与 UI 展示中
提供可筛选的基础信息。
主要功能包括：
- 提供包含 `tool_calling` / `deprecated` 等标记的模型元数据
- 生成面向 UI 的模型名称列表与兼容列表

关键组件：
- `ANTHROPIC_MODELS_DETAILED`：元数据主列表
- `ANTHROPIC_MODELS`：可用且支持工具调用的模型名集合

设计背景：统一模型来源，便于过滤不支持或弃用模型并保持兼容性。
注意事项：模型是否可用以元数据标记为准，动态可用性由上层刷新机制保证。
"""

from .model_metadata import create_model_metadata

ANTHROPIC_MODELS_DETAILED = [
    # 支持工具调用的模型
    create_model_metadata(provider="Anthropic", name="claude-opus-4-5-20251101", icon="Anthropic", tool_calling=True),
    create_model_metadata(provider="Anthropic", name="claude-haiku-4-5-20251001", icon="Anthropic", tool_calling=True),
    create_model_metadata(provider="Anthropic", name="claude-sonnet-4-5-20250929", icon="Anthropic", tool_calling=True),
    create_model_metadata(provider="Anthropic", name="claude-opus-4-1-20250805", icon="Anthropic", tool_calling=True),
    create_model_metadata(provider="Anthropic", name="claude-opus-4-20250514", icon="Anthropic", tool_calling=True),
    create_model_metadata(provider="Anthropic", name="claude-sonnet-4-20250514", icon="Anthropic", tool_calling=True),
    create_model_metadata(provider="Anthropic", name="claude-3-5-haiku-20241022", icon="Anthropic", tool_calling=True),
    create_model_metadata(provider="Anthropic", name="claude-3-haiku-20240307", icon="Anthropic", tool_calling=True),
    # 已弃用模型（保留以兼容旧流程）
    create_model_metadata(
        provider="Anthropic", name="claude-3-7-sonnet-latest", icon="Anthropic", tool_calling=True, deprecated=True
    ),
    create_model_metadata(
        provider="Anthropic", name="claude-3-5-sonnet-latest", icon="Anthropic", tool_calling=True, deprecated=True
    ),
    create_model_metadata(
        provider="Anthropic", name="claude-3-5-haiku-latest", icon="Anthropic", tool_calling=True, deprecated=True
    ),
    create_model_metadata(
        provider="Anthropic", name="claude-3-opus-latest", icon="Anthropic", tool_calling=True, deprecated=True
    ),
    create_model_metadata(
        provider="Anthropic", name="claude-3-sonnet-20240229", icon="Anthropic", tool_calling=True, deprecated=True
    ),
    create_model_metadata(
        provider="Anthropic", name="claude-2.1", icon="Anthropic", tool_calling=False, deprecated=True
    ),
    create_model_metadata(
        provider="Anthropic", name="claude-2.0", icon="Anthropic", tool_calling=False, deprecated=True
    ),
    create_model_metadata(
        provider="Anthropic", name="claude-3-5-sonnet-20240620", icon="Anthropic", tool_calling=True, deprecated=True
    ),
    create_model_metadata(
        provider="Anthropic", name="claude-3-5-sonnet-20241022", icon="Anthropic", tool_calling=True, deprecated=True
    ),
]

# 可用列表：仅保留未弃用且支持工具调用的模型
ANTHROPIC_MODELS = [
    metadata["name"]
    for metadata in ANTHROPIC_MODELS_DETAILED
    if not metadata.get("deprecated", False) and metadata.get("tool_calling", False)
]

# 支持工具调用（含弃用）列表
TOOL_CALLING_SUPPORTED_ANTHROPIC_MODELS = [
    metadata["name"] for metadata in ANTHROPIC_MODELS_DETAILED if metadata.get("tool_calling", False)
]

# 不支持工具调用列表
TOOL_CALLING_UNSUPPORTED_ANTHROPIC_MODELS = [
    metadata["name"] for metadata in ANTHROPIC_MODELS_DETAILED if not metadata.get("tool_calling", False)
]

# 弃用模型列表（用于兼容与提示）
DEPRECATED_MODELS = [metadata["name"] for metadata in ANTHROPIC_MODELS_DETAILED if metadata.get("deprecated", False)]


DEFAULT_ANTHROPIC_API_URL = "https://api.anthropic.com"
