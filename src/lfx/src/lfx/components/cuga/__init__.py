"""
模块名称：Cuga 组件包入口

本模块提供 Cuga 组件的延迟导入入口，主要用于在可选依赖存在时才加载实现，
降低启动时的导入成本。
主要功能包括：
- 通过 `__getattr__` 延迟加载组件实现
- 维护对外公开的符号列表

关键组件：
- `CugaComponent`：位于 `cuga_agent`

设计背景：Cuga 组件依赖较多，采用惰性导入以避免无用依赖报错。
注意事项：未安装依赖时会抛 `AttributeError`，上层需处理。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lfx.components._importing import import_mod

if TYPE_CHECKING:
    from .cuga_agent import CugaComponent

_dynamic_imports = {
    "CugaComponent": "cuga_agent",
}

__all__ = [
    "CugaComponent",
]


def __getattr__(attr_name: str) -> Any:
    """按需延迟导入组件符号。

    契约：仅允许 `_dynamic_imports` 中声明的属性被访问。
    失败语义：模块缺失或导入失败会抛 `AttributeError`。
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
    """返回对外公开的符号列表。"""
    return list(__all__)
