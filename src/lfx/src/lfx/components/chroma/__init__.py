"""
模块名称：Chroma 组件包入口

本模块提供 Chroma 组件的延迟导入入口，降低可选依赖对启动的影响。主要功能包括：
- 作为 `lfx.components.chroma` 的包级入口
- 延迟加载 `ChromaVectorStoreComponent`

关键组件：
- `ChromaVectorStoreComponent`

设计背景：Chroma 依赖可选包，需在实际使用时再加载。
使用场景：上层按需引用 Chroma 向量库组件。
注意事项：访问未知属性将抛 `AttributeError`。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lfx.components._importing import import_mod

if TYPE_CHECKING:
    from .chroma import ChromaVectorStoreComponent

_dynamic_imports = {
    "ChromaVectorStoreComponent": "chroma",
}

__all__ = [
    "ChromaVectorStoreComponent",
]


def __getattr__(attr_name: str) -> Any:
    """按需延迟导入 Chroma 组件

    契约：输入属性名并返回对应组件对象；副作用：缓存到 `globals()`；
    失败语义：属性未知或导入失败抛 `AttributeError`。
    关键路径：1) 校验属性名 2) 动态导入 3) 缓存并返回。
    决策：采用延迟导入而非模块加载时全部导入。
    问题：Chroma 依赖为可选包，提前导入会导致启动失败或变慢。
    方案：在首次访问时加载并缓存。
    代价：首次访问有额外导入开销。
    重评：当依赖统一安装或需静态导入时。
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
    """返回可导出的组件名列表

    契约：返回 `__all__`；副作用：无；失败语义：无。
    关键路径：直接返回导出列表。
    决策：以 `__all__` 作为公开 API 的唯一来源。
    问题：需要与延迟导入的可见属性保持一致。
    方案：`__dir__` 委托 `__all__`。
    代价：需同步维护 `__all__`。
    重评：当导出列表改为自动生成时。
    """
    return list(__all__)
