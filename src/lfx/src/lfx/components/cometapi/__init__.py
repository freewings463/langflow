"""
模块名称：`CometAPI` 组件子包

本子包提供 `CometAPI` 组件的动态导入入口，用于延迟加载依赖并降低启动成本。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lfx.components._importing import import_mod

if TYPE_CHECKING:
    from lfx.components.cometapi.cometapi import CometAPIComponent

_dynamic_imports = {
    "CometAPIComponent": "cometapi",
}

__all__ = ["CometAPIComponent"]


def __getattr__(attr_name: str) -> Any:
    """按需延迟导入 `CometAPI` 组件

    契约：
    - 输入：属性名
    - 输出：对应组件类或模块对象
    - 副作用：将已导入对象写入 `globals()` 缓存
    - 失败语义：不存在或导入失败时抛 `AttributeError`
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
    """返回该模块可导出的符号列表

    契约：
    - 输入：无
    - 输出：符号名列表
    - 副作用：无
    - 失败语义：无
    """
    return list(__all__)
