"""
模块名称：lfx.components.docling

本模块提供 Docling 组件的懒加载入口，并根据云环境禁用本地依赖组件。
主要功能包括：
- 在云环境中隐藏需要本地依赖的组件
- 按需加载 Docling 组件以降低启动成本

关键组件：
- `_get_available_components`：按环境筛选组件列表
- `_get_dynamic_imports`：按环境筛选动态导入映射
- `__getattr__`：按名称懒加载组件

设计背景：Docling 本地依赖较重，云环境无法安装 OCR 依赖
使用场景：组件注册与动态加载
注意事项：云环境仅暴露 `DoclingRemoteComponent`
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lfx.components._importing import import_mod
from lfx.utils.validate_cloud import is_astra_cloud_environment

if TYPE_CHECKING:
    from .chunk_docling_document import ChunkDoclingDocumentComponent  # noqa: F401
    from .docling_inline import DoclingInlineComponent  # noqa: F401
    from .docling_remote import DoclingRemoteComponent  # noqa: F401
    from .export_docling_document import ExportDoclingDocumentComponent  # noqa: F401

_all_components = [
    "ChunkDoclingDocumentComponent",
    "DoclingInlineComponent",
    "DoclingRemoteComponent",
    "ExportDoclingDocumentComponent",
]

_all_dynamic_imports = {
    "ChunkDoclingDocumentComponent": "chunk_docling_document",
    "DoclingInlineComponent": "docling_inline",
    "DoclingRemoteComponent": "docling_remote",
    "ExportDoclingDocumentComponent": "export_docling_document",
}

# 注意：依赖本地 Docling/EasyOCR 的组件在云环境禁用。
_cloud_disabled_components = {
    "ChunkDoclingDocumentComponent",
    "DoclingInlineComponent",
    "ExportDoclingDocumentComponent",
}


def _get_available_components() -> list[str]:
    """获取可用组件列表，并过滤云环境禁用项。

    契约：返回组件名列表。
    失败语义：无显式异常。
    """
    if is_astra_cloud_environment():
        # 注意：云环境仅展示 Docling Serve 远程组件。
        return [comp for comp in _all_components if comp not in _cloud_disabled_components]
    return _all_components


def _get_dynamic_imports() -> dict[str, str]:
    """获取动态导入映射，并过滤云环境禁用项。

    契约：返回组件名到模块名的映射。
    失败语义：无显式异常。
    """
    if is_astra_cloud_environment():
        # 注意：云环境仅允许 Docling Serve 远程组件。
        return {k: v for k, v in _all_dynamic_imports.items() if k not in _cloud_disabled_components}
    return _all_dynamic_imports


# 注意：根据环境动态设置导出列表与导入映射。
__all__: list[str] = _get_available_components()  # noqa: PLE0605
_dynamic_imports: dict[str, str] = _get_dynamic_imports()


def __getattr__(attr_name: str) -> Any:
    """按属性名懒加载 Docling 组件。

    契约：`attr_name` 必须在可用组件映射中。
    副作用：动态导入模块并缓存到 `globals()`。
    失败语义：组件不可用或导入失败时抛 `AttributeError`。
    """
    # 注意：云环境禁用的组件直接拒绝。
    if is_astra_cloud_environment() and attr_name in _cloud_disabled_components:
        msg = f"module '{__name__}' has no attribute '{attr_name}'"
        raise AttributeError(msg)

    if attr_name not in _all_dynamic_imports:
        msg = f"module '{__name__}' has no attribute '{attr_name}'"
        raise AttributeError(msg)
    try:
        result = import_mod(attr_name, _all_dynamic_imports[attr_name], __spec__.parent)
    except (ModuleNotFoundError, ImportError, AttributeError) as e:
        msg = f"Could not import '{attr_name}' from '{__name__}': {e}"
        raise AttributeError(msg) from e
    globals()[attr_name] = result
    return result


def __dir__() -> list[str]:
    """暴露当前环境可用组件名列表。"""
    return _get_available_components()
