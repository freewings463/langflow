"""
模块名称：Groq 组件包入口

本模块提供 Groq 相关组件的懒加载入口，主要用于减少未使用组件时的导入开销。主要功能包括：
- 按需导入 `GroqModel`

关键组件：
- `__getattr__`：按需导入组件类

设计背景：组件加载路径需要保持轻量，避免 CLI/UI 启动额外依赖。
注意事项：仅导出 `__all__` 中声明的组件名称。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lfx.components._importing import import_mod

if TYPE_CHECKING:
    from .groq import GroqModel

_dynamic_imports = {
    "GroqModel": "groq",
}

__all__ = [
    "GroqModel",
]


def __getattr__(attr_name: str) -> Any:
    """按需导入 Groq 组件。

    契约：仅允许 `_dynamic_imports` 中声明的属性名。
    失败语义：导入失败或属性不存在时抛 `AttributeError`。
    副作用：首次访问时触发模块导入并缓存到 `globals()`。
    """
    if attr_name not in _dynamic_imports:
        msg = f"module '{__name__}' has no attribute '{attr_name}'"
        raise AttributeError(msg)
    try:
        result = import_mod(attr_name, _dynamic_imports[attr_name], __spec__.parent)
    except (ModuleNotFoundError, ImportError, AttributeError) as e:
        msg = f"Could not import '{attr_name}' from '{__name__}': {e}"
        raise AttributeError(msg) from e
    globals()[attr_name] = result
    return result


def __dir__() -> list[str]:
    """返回可导出的公共属性列表。"""
    return list(__all__)
