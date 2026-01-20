"""
模块名称：constants

本模块定义了Langflow应用的常量，主要用于向后兼容。
主要功能包括：
- 从新的lfx.utils.constants模块导入所有常量
- 定义Langflow特定的常量

设计背景：为了支持从旧版langflow到新版lfx的架构迁移，保持API兼容性
注意事项：新代码应直接使用 lfx.utils.constants 模块
"""

from lfx.utils.constants import (
    ANTHROPIC_MODELS,
    CHAT_OPENAI_MODELS,
    DEFAULT_PYTHON_FUNCTION,
    DIRECT_TYPES,
    LOADERS_INFO,
    MESSAGE_SENDER_AI,
    MESSAGE_SENDER_NAME_AI,
    MESSAGE_SENDER_NAME_USER,
    MESSAGE_SENDER_USER,
    OPENAI_MODELS,
    PYTHON_BASIC_TYPES,
    REASONING_OPENAI_MODELS,
)

# Langflow特定常量：全局变量HTTP头前缀
LANGFLOW_GLOBAL_VAR_HEADER_PREFIX = "x-langflow-global-var-"

__all__ = [
    "ANTHROPIC_MODELS",
    "CHAT_OPENAI_MODELS",
    "DEFAULT_PYTHON_FUNCTION",
    "DIRECT_TYPES",
    "LANGFLOW_GLOBAL_VAR_HEADER_PREFIX",
    "LOADERS_INFO",
    "MESSAGE_SENDER_AI",
    "MESSAGE_SENDER_NAME_AI",
    "MESSAGE_SENDER_NAME_USER",
    "MESSAGE_SENDER_USER",
    "OPENAI_MODELS",
    "PYTHON_BASIC_TYPES",
    "REASONING_OPENAI_MODELS",
]
