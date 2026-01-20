"""
模块名称：Vectara 组件导出门面

模块目的：提供 Vectara 组件的延迟导入与统一导出。
使用场景：组件发现/注册阶段仅需导出符号而不立即加载依赖。
主要功能包括：
- 通过 `__getattr__` 按需导入并缓存组件类
- 维护 `__all__`/`__dir__` 的稳定导出集合

关键组件：
- `_dynamic_imports`：导入映射表
- `__getattr__`：按需导入入口

设计背景：组件扫描与运行时解耦，避免启动阶段拉起全部依赖。
注意：导入失败会统一转为 `AttributeError`，调用方应捕获并降级处理。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lfx.components._importing import import_mod

if TYPE_CHECKING:
    from .vectara import VectaraVectorStoreComponent
    from .vectara_rag import VectaraRagComponent

_dynamic_imports = {
    "VectaraVectorStoreComponent": "vectara",
    "VectaraRagComponent": "vectara_rag",
}

__all__ = [
    "VectaraRagComponent",
    "VectaraVectorStoreComponent",
]


def __getattr__(attr_name: str) -> Any:
    """契约：仅支持 `_dynamic_imports` 中声明的属性名并返回对应组件类。

    失败语义：未注册或导入异常时抛 `AttributeError`。
    副作用：首次访问会导入模块并写入 `globals()` 缓存。
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
    return list(__all__)
