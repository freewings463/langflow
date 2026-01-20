"""
模块名称：`altk` 组件包入口

本模块提供 `lfx.components.altk` 的懒加载导出，用于按需装载 ALTK 相关组件。
主要功能包括：
- 通过 `__getattr__` 动态导入组件，降低启动开销
- 统一 `__all__` 对外导出列表

关键组件：
- `ALTKAgentComponent`：ALTK 代理组件（延迟加载）

设计背景：避免在模块导入阶段加载重依赖，缩短组件注册时间。
注意事项：访问未注册的导出会抛 `AttributeError`，调用方需检查名称。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lfx.components._importing import import_mod

if TYPE_CHECKING:
    from .altk_agent import ALTKAgentComponent

_dynamic_imports = {
    "ALTKAgentComponent": "altk_agent",
}

__all__ = [
    "ALTKAgentComponent",
]


def __getattr__(attr_name: str) -> Any:
    """按需导入 `altk` 组件。

    契约：`attr_name` 必须在 `_dynamic_imports` 中注册。
    副作用：动态导入模块并写入 `globals()`。
    失败语义：缺少注册或导入失败时抛 `AttributeError`。
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
    """提供可见导出列表，便于 `dir()` 与 IDE 补全。"""
    return list(__all__)
