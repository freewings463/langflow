"""
模块名称：Groq 模型静态兜底列表

本模块提供 Groq 模型元数据的静态兜底列表，主要用于动态发现不可用时的降级。
主要功能包括：
- 提供稳定的生产模型与弃用模型集合
- 标记不支持的非 LLM 模型（如音频/TTS/安全模型）
- 生成兼容旧接口的模型名列表

关键组件：
- `GROQ_MODELS_DETAILED`：兜底元数据主列表

设计背景：动态模型发现是主路径，但需要稳定的离线兜底。
注意事项：当 `groq_model_discovery.py` 可用时应优先使用动态结果。
"""

from .model_metadata import create_model_metadata

# 统一元数据兜底：
# - 动态发现失败时使用
# - 动态系统会从 Groq API 拉取、测试工具调用并缓存 24 小时
# - 兜底列表仅保留稳定生产模型、弃用模型与非 LLM 标记
# 手工更新日期：2025-01-06
GROQ_MODELS_DETAILED = [
    # ===== 兜底生产模型 =====
    # 稳定模型，尽量避免被移除
    create_model_metadata(provider="Groq", name="llama-3.1-8b-instant", icon="Groq", tool_calling=True),
    create_model_metadata(provider="Groq", name="llama-3.3-70b-versatile", icon="Groq", tool_calling=True),
    # ===== 弃用模型 =====
    # 用于兼容旧流程；在 UI 中会标记为弃用
    create_model_metadata(  # Google - 已移除
        provider="Groq", name="gemma2-9b-it", icon="Groq", deprecated=True
    ),
    create_model_metadata(  # Google
        provider="Groq", name="gemma-7b-it", icon="Groq", deprecated=True
    ),
    create_model_metadata(  # Meta - 已移除
        provider="Groq", name="llama3-70b-8192", icon="Groq", deprecated=True
    ),
    create_model_metadata(  # Meta - 已移除
        provider="Groq", name="llama3-8b-8192", icon="Groq", deprecated=True
    ),
    create_model_metadata(  # Meta - 已移除，替换为 llama-guard-4-12b
        provider="Groq", name="llama-guard-3-8b", icon="Groq", deprecated=True
    ),
    create_model_metadata(  # Meta - 已移除
        provider="Groq", name="llama-3.2-1b-preview", icon="Groq", deprecated=True
    ),
    create_model_metadata(  # Meta - 已移除
        provider="Groq", name="llama-3.2-3b-preview", icon="Groq", deprecated=True
    ),
    create_model_metadata(  # Meta - 已移除
        provider="Groq", name="llama-3.2-11b-vision-preview", icon="Groq", deprecated=True
    ),
    create_model_metadata(  # Meta - 已移除
        provider="Groq", name="llama-3.2-90b-vision-preview", icon="Groq", deprecated=True
    ),
    create_model_metadata(  # Meta - 已移除
        provider="Groq", name="llama-3.3-70b-specdec", icon="Groq", deprecated=True
    ),
    create_model_metadata(  # Alibaba - 已移除，替换为 qwen/qwen3-32b
        provider="Groq", name="qwen-qwq-32b", icon="Groq", deprecated=True
    ),
    create_model_metadata(  # Alibaba - 已移除
        provider="Groq", name="qwen-2.5-coder-32b", icon="Groq", deprecated=True
    ),
    create_model_metadata(  # Alibaba - 已移除
        provider="Groq", name="qwen-2.5-32b", icon="Groq", deprecated=True
    ),
    create_model_metadata(  # DeepSeek - 已移除
        provider="Groq", name="deepseek-r1-distill-qwen-32b", icon="Groq", deprecated=True
    ),
    create_model_metadata(  # DeepSeek - 已移除
        provider="Groq", name="deepseek-r1-distill-llama-70b", icon="Groq", deprecated=True
    ),
    create_model_metadata(  # Groq
        provider="Groq", name="llama3-groq-70b-8192-tool-use-preview", icon="Groq", deprecated=True
    ),
    create_model_metadata(  # Groq
        provider="Groq", name="llama3-groq-8b-8192-tool-use-preview", icon="Groq", deprecated=True
    ),
    create_model_metadata(  # Meta
        provider="Groq", name="llama-3.1-70b-versatile", icon="Groq", deprecated=True
    ),
    create_model_metadata(  # Mistral
        provider="Groq", name="mixtral-8x7b-32768", icon="Groq", deprecated=True
    ),
    # ===== 不支持模型 =====
    # 非 LLM（音频/TTS/安全）模型，不应出现在 LLM 列表中
    create_model_metadata(  # Mistral
        provider="Groq", name="mistral-saba-24b", icon="Groq", not_supported=True
    ),
    create_model_metadata(  # Playht, Inc
        provider="Groq", name="playai-tts", icon="Groq", not_supported=True
    ),
    create_model_metadata(  # Playht, Inc
        provider="Groq", name="playai-tts-arabic", icon="Groq", not_supported=True
    ),
    create_model_metadata(  # OpenAI
        provider="Groq", name="whisper-large-v3", icon="Groq", not_supported=True
    ),
    create_model_metadata(  # OpenAI
        provider="Groq", name="whisper-large-v3-turbo", icon="Groq", not_supported=True
    ),
    create_model_metadata(  # Hugging Face
        provider="Groq", name="distil-whisper-large-v3-en", icon="Groq", not_supported=True
    ),
    create_model_metadata(  # Meta
        provider="Groq", name="meta-llama/llama-guard-4-12b", icon="Groq", not_supported=True
    ),
    create_model_metadata(  # Meta
        provider="Groq", name="meta-llama/llama-prompt-guard-2-86m", icon="Groq", not_supported=True
    ),
    create_model_metadata(  # Meta
        provider="Groq", name="meta-llama/llama-prompt-guard-2-22m", icon="Groq", not_supported=True
    ),
    create_model_metadata(  # OpenAI
        provider="Groq", name="openai/gpt-oss-safeguard-20b", icon="Groq", not_supported=True
    ),
]

# 兼容列表：从元数据生成旧接口需要的列表
GROQ_PRODUCTION_MODELS = [
    metadata["name"]
    for metadata in GROQ_MODELS_DETAILED
    if not metadata.get("preview", False)
    and not metadata.get("deprecated", False)
    and not metadata.get("not_supported", False)
]

GROQ_PREVIEW_MODELS = [metadata["name"] for metadata in GROQ_MODELS_DETAILED if metadata.get("preview", False)]

DEPRECATED_GROQ_MODELS = [metadata["name"] for metadata in GROQ_MODELS_DETAILED if metadata.get("deprecated", False)]

UNSUPPORTED_GROQ_MODELS = [
    metadata["name"] for metadata in GROQ_MODELS_DETAILED if metadata.get("not_supported", False)
]

TOOL_CALLING_UNSUPPORTED_GROQ_MODELS = [
    metadata["name"]
    for metadata in GROQ_MODELS_DETAILED
    if not metadata.get("tool_calling", False)
    and not metadata.get("not_supported", False)
    and not metadata.get("deprecated", False)
]

# 当前可用模型合并列表（兼容旧接口）
GROQ_MODELS = GROQ_PRODUCTION_MODELS + GROQ_PREVIEW_MODELS

# 向后兼容别名
MODEL_NAMES = GROQ_MODELS
