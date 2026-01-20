"""
模块名称：Elastic 组件导出

本模块提供 Elasticsearch/OpenSearch 组件的延迟导入入口。
注意事项：新增组件需同步更新 `__all__` 与 `_dynamic_imports`。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lfx.components._importing import import_mod

if TYPE_CHECKING:
    from .elasticsearch import ElasticsearchVectorStoreComponent
    from .opensearch import OpenSearchVectorStoreComponent

_dynamic_imports = {
    "ElasticsearchVectorStoreComponent": "elasticsearch",
    "OpenSearchVectorStoreComponent": "opensearch",
}

__all__ = [
    "ElasticsearchVectorStoreComponent",
    "OpenSearchVectorStoreComponent",
]


def __getattr__(attr_name: str) -> Any:
    """按需导入 Elastic 组件。

    契约：输入属性名，输出对应组件对象。
    副作用：首次访问会执行动态导入并缓存到模块全局。
    失败语义：未注册属性抛 `AttributeError`。
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
    """返回模块可用的导出项列表。"""
    return list(__all__)
