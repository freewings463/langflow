from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lfx.components._importing import import_mod

if TYPE_CHECKING:
    # 仅用于类型检查，避免运行时导入开销
    from .ollama import ChatOllamaComponent
    from .ollama_embeddings import OllamaEmbeddingsComponent

# 动态导入映射：公开名称 -> 模块名
_dynamic_imports = {
    "ChatOllamaComponent": "ollama",
    "OllamaEmbeddingsComponent": "ollama_embeddings",
}

__all__ = [
    "ChatOllamaComponent",
    "OllamaEmbeddingsComponent",
]


def __getattr__(attr_name: str) -> Any:
    """Lazily import ollama components on attribute access."""
    # 仅当访问未加载的属性时才进行导入
    if attr_name not in _dynamic_imports:
        msg = f"module '{__name__}' has no attribute '{attr_name}'"
        raise AttributeError(msg)
    try:
        result = import_mod(attr_name, _dynamic_imports[attr_name], __spec__.parent)
    except (ModuleNotFoundError, ImportError, AttributeError) as e:
        msg = f"Could not import '{attr_name}' from '{__name__}': {e}"
        raise AttributeError(msg) from e
    # 缓存到模块全局，避免重复导入
    globals()[attr_name] = result
    return result


def __dir__() -> list[str]:
    # 让 dir() 只展示导出的符号
    return list(__all__)
