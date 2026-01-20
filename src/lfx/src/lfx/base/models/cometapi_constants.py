"""
模块名称：CometAPI 模型常量

本模块提供 CometAPI 的默认模型名称列表，主要用于当 API 不可用或未提供 API key
时的兜底选项。
主要功能包括：
- 维护 CometAPI 可用模型名的静态集合
- 提供向后兼容的模型名称别名

关键组件：
- `COMETAPI_MODELS`：模型名列表

设计背景：在无法动态获取模型列表时提供稳定的退化路径。
注意事项：该列表可能滞后于实时 API，需结合上层刷新机制。
"""

from typing import Final

# CometAPI 可用模型列表（基于已知 API 提供项）
COMETAPI_MODELS: Final[list[str]] = [
    # GPT series
    "gpt-5-chat-latest",
    "chatgpt-4o-latest",
    "gpt-5-mini",
    "gpt-5-nano",
    "gpt-5",
    "gpt-4.1-mini",
    "gpt-4.1-nano",
    "gpt-4.1",
    "gpt-4o-mini",
    "o4-mini-2025-04-16",
    "o3-pro-2025-06-10",
    # Claude series
    "claude-sonnet-4-5-20250929",
    "claude-opus-4-1-20250805",
    "claude-opus-4-1-20250805-thinking",
    "claude-sonnet-4-20250514",
    "claude-sonnet-4-20250514-thinking",
    "claude-3-7-sonnet-latest",
    "claude-3-5-haiku-latest",
    # Gemini series
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    # Grok series
    "grok-4-0709",
    "grok-3",
    "grok-3-mini",
    "grok-2-image-1212",
    # DeepSeek series
    "deepseek-v3.1",
    "deepseek-v3",
    "deepseek-r1-0528",
    "deepseek-chat",
    "deepseek-reasoner",
    # Qwen series
    "qwen3-30b-a3b",
    "qwen3-coder-plus-2025-07-22",
]

# 向后兼容别名
MODEL_NAMES: Final[list[str]] = COMETAPI_MODELS
