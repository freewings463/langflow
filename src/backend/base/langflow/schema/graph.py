"""
模块名称：`graph` 兼容导出

本模块转发 `lfx.schema.graph` 的输入与调整结构，主要用于旧路径兼容。主要功能包括：
- 暴露 `InputValue` / `Tweaks`

关键组件：
- InputValue
- Tweaks

设计背景：历史代码仍依赖 `langflow.schema.graph`。
注意事项：仅导出符号，行为由 `lfx` 实现决定。
"""

from lfx.schema.graph import InputValue, Tweaks

__all__ = ["InputValue", "Tweaks"]
