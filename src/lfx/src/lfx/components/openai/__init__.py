"""模块名称：OpenAI 组件懒加载出口

本模块提供 OpenAI 相关组件的惰性导出入口，供组件发现与注册流程统一引用。
使用场景：在扫描组件目录或运行时按需加载 OpenAI 组件时。
主要功能：将组件名映射到延迟导入逻辑，避免未使用时加载依赖。

关键组件：
- __getattr__：按需导入组件并缓存到模块命名空间

设计背景：降低可选依赖导入成本与失败面，避免启动阶段硬依赖
注意事项：仅导出 `_dynamic_imports` 中声明的组件名
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lfx.components._importing import import_mod

if TYPE_CHECKING:
    from lfx.components.openai.openai import OpenAIEmbeddingsComponent
    from lfx.components.openai.openai_chat_model import OpenAIModelComponent

# 注意：组件名到模块名的映射用于惰性导入，避免未使用时加载依赖。
_dynamic_imports = {
    "OpenAIEmbeddingsComponent": "openai",
    "OpenAIModelComponent": "openai_chat_model",
}

__all__ = [
    "OpenAIEmbeddingsComponent",
    "OpenAIModelComponent",
]


def __getattr__(attr_name: str) -> Any:
    """按需导入 OpenAI 组件并缓存。

    契约：仅允许 `_dynamic_imports` 中的组件名；失败时抛 `AttributeError`
    副作用：成功导入后写入 `globals()` 以缓存结果
    失败语义：不存在的属性或导入失败均转为 `AttributeError`
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
    """返回可公开的组件名称列表。"""
    return list(__all__)
