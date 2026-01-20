"""
模块名称：`Pinecone` 组件懒加载入口

本模块提供 `Pinecone` 组件的按需导入，避免在未使用时加载依赖。
主要功能包括：
- 组件名到模块名的映射
- 属性访问时延迟导入并缓存
- 通过 `__all__` 与 `__dir__` 统一导出入口

关键组件：
- `_dynamic_imports`：组件名映射表
- `__getattr__`：懒加载入口
- `__dir__`：导出成员列表

设计背景：降低初始化成本并稳定导入路径。
注意事项：仅支持映射表内的组件名，其他访问会抛 `AttributeError`。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lfx.components._importing import import_mod

if TYPE_CHECKING:
    from .pinecone import PineconeVectorStoreComponent

_dynamic_imports = {
    "PineconeVectorStoreComponent": "pinecone",
}

__all__ = [
    "PineconeVectorStoreComponent",
]


def __getattr__(attr_name: str) -> Any:
    """按名称懒加载组件并缓存到模块全局。

    契约：输入组件类名，返回对应类对象。
    失败语义：名称不存在或导入失败时抛 `AttributeError`。
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
    """返回可见成员列表，保持反射与自动补全一致。"""
    return list(__all__)
