"""
模块名称：Mistral 组件导出门面

模块目的：提供 Mistral 组件的延迟导入与统一导出。
使用场景：组件发现/注册阶段仅需导出符号而不立即加载依赖。
主要功能包括：
- 通过 `__getattr__` 按需导入并缓存组件类
- 维护 `__all__`/`__dir__` 的稳定导出集合

关键组件：
- `_dynamic_imports`：导入映射表
- `__getattr__`：按需导入入口

设计背景：组件可能在启动时被扫描但不一定立即使用，需要降低依赖加载风险。
注意：导入失败会统一转为 `AttributeError`，调用方应在发现阶段捕获并降级。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lfx.components._importing import import_mod

if TYPE_CHECKING:
    from .mistral import MistralAIModelComponent
    from .mistral_embeddings import MistralAIEmbeddingsComponent

_dynamic_imports = {
    "MistralAIModelComponent": "mistral",
    "MistralAIEmbeddingsComponent": "mistral_embeddings",
}

__all__ = [
    "MistralAIEmbeddingsComponent",
    "MistralAIModelComponent",
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
