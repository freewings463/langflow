"""
模块名称：IBM 组件包入口

本模块提供 IBM Watsonx 相关组件的懒加载入口，主要用于减少未使用组件时的导入开销。主要功能包括：
- 按需导入 `WatsonxAIComponent`
- 按需导入 `WatsonxEmbeddingsComponent`

关键组件：
- `__getattr__`：按需导入组件类

设计背景：组件初始化可能触发 SDK 依赖加载，采用懒加载降低启动成本。
注意事项：仅导出 `__all__` 中声明的组件名称。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lfx.components._importing import import_mod

if TYPE_CHECKING:
    from lfx.components.ibm.watsonx import WatsonxAIComponent
    from lfx.components.ibm.watsonx_embeddings import WatsonxEmbeddingsComponent

_dynamic_imports = {
    "WatsonxAIComponent": "watsonx",
    "WatsonxEmbeddingsComponent": "watsonx_embeddings",
}

__all__ = ["WatsonxAIComponent", "WatsonxEmbeddingsComponent"]


def __getattr__(attr_name: str) -> Any:
    """按需导入 IBM 组件。

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
