"""
模块名称：Google Generative AI 模型元数据

本模块维护 Google Generative AI 相关模型的静态元数据，主要用于统一模型注册与
UI 选择项生成。
主要功能包括：
- 提供包含预览/不支持标记的模型元数据
- 生成模型名称列表

关键组件：
- `GOOGLE_GENERATIVE_AI_MODELS_DETAILED`：元数据主列表

设计背景：以静态元数据作为 UI 与兼容层的稳定来源。
注意事项：标记 `preview`/`not_supported` 的模型默认不会被上层选择。
"""

from .model_metadata import create_model_metadata

# 统一元数据：作为唯一事实来源
GOOGLE_GENERATIVE_AI_MODELS_DETAILED = [
    # GEMINI 1.5（稳定）
    create_model_metadata(
        provider="Google Generative AI",
        name="gemini-1.5-pro",
        icon="GoogleGenerativeAI",
        tool_calling=True,
    ),
    create_model_metadata(
        provider="Google Generative AI",
        name="gemini-1.5-flash",
        icon="GoogleGenerativeAI",
        tool_calling=True,
    ),
    create_model_metadata(
        provider="Google Generative AI", name="gemini-1.5-flash-8b", icon="GoogleGenerativeAI", tool_calling=True
    ),
    # GEMINI 2.0（稳定）
    create_model_metadata(
        provider="Google Generative AI",
        name="gemini-2.0-flash-lite",
        icon="GoogleGenerativeAI",
        tool_calling=True,
    ),
    # GEMINI 2.5（预期/未正式发布）
    create_model_metadata(
        provider="Google Generative AI",
        name="gemini-2.5-pro",
        icon="GoogleGenerativeAI",
        tool_calling=True,
        preview=True,
        not_supported=True,
    ),
    create_model_metadata(
        provider="Google Generative AI",
        name="gemini-2.5-flash",
        icon="GoogleGenerativeAI",
        tool_calling=True,
        preview=True,
        not_supported=True,
    ),
    create_model_metadata(
        provider="Google Generative AI",
        name="gemini-2.5-flash-lite",
        icon="GoogleGenerativeAI",
        tool_calling=True,
        preview=True,
        not_supported=True,
    ),
    # 预览模型
    create_model_metadata(
        provider="Google Generative AI",
        name="gemini-2.0-flash",
        icon="GoogleGenerativeAI",
        tool_calling=True,
        preview=True,
    ),
    create_model_metadata(
        provider="Google Generative AI",
        name="gemini-exp-1206",
        icon="GoogleGenerativeAI",
        tool_calling=True,
        preview=True,
    ),
    create_model_metadata(
        provider="Google Generative AI",
        name="gemini-2.0-flash-thinking-exp-01-21",
        icon="GoogleGenerativeAI",
        tool_calling=True,
        preview=True,
    ),
    create_model_metadata(
        provider="Google Generative AI",
        name="learnlm-1.5-pro-experimental",
        icon="GoogleGenerativeAI",
        tool_calling=True,
        preview=True,
    ),
    # GEMMA
    create_model_metadata(
        provider="Google Generative AI", name="gemma-2-2b", icon="GoogleGenerativeAI", tool_calling=True
    ),
    create_model_metadata(
        provider="Google Generative AI", name="gemma-2-9b", icon="GoogleGenerativeAI", tool_calling=True
    ),
    create_model_metadata(
        provider="Google Generative AI", name="gemma-2-27b", icon="GoogleGenerativeAI", tool_calling=True
    ),
]

# 模型名称列表（含预览/不支持标记的模型）
GOOGLE_GENERATIVE_AI_MODELS = [metadata["name"] for metadata in GOOGLE_GENERATIVE_AI_MODELS_DETAILED]
