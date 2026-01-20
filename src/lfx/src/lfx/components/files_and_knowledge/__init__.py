"""
模块名称：files_and_knowledge 组件包入口

本模块提供 `lfx.components.files_and_knowledge` 的懒加载导出，集中管理文件与知识库相关组件。
主要功能包括：
- 通过 `__getattr__` 动态导入组件，降低启动开销
- 统一对外导出列表，便于组件发现与注册

关键组件：
- DirectoryComponent：目录加载
- FileComponent：文件读取
- KnowledgeIngestionComponent：知识库写入
- KnowledgeRetrievalComponent：知识库检索
- SaveToFileComponent：文件保存

设计背景：文件/知识相关组件依赖较多，按需加载可减少导入成本。
注意事项：访问未注册的导出名会抛 `AttributeError`。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lfx.components._importing import import_mod

if TYPE_CHECKING:
    from lfx.components.files_and_knowledge.directory import DirectoryComponent
    from lfx.components.files_and_knowledge.file import FileComponent
    from lfx.components.files_and_knowledge.ingestion import KnowledgeIngestionComponent
    from lfx.components.files_and_knowledge.retrieval import KnowledgeRetrievalComponent
    from lfx.components.files_and_knowledge.save_file import SaveToFileComponent


_dynamic_imports = {
    "DirectoryComponent": "directory",
    "FileComponent": "file",
    "KnowledgeIngestionComponent": "ingestion",
    "KnowledgeRetrievalComponent": "retrieval",
    "SaveToFileComponent": "save_file",
}

__all__ = [
    "DirectoryComponent",
    "FileComponent",
    "KnowledgeIngestionComponent",
    "KnowledgeRetrievalComponent",
    "SaveToFileComponent",
]


def __getattr__(attr_name: str) -> Any:
    """按需导入文件与知识库组件。

    契约：`attr_name` 必须在 `_dynamic_imports` 中注册。
    副作用：动态导入模块并写入 `globals()`。
    失败语义：缺少注册或导入失败时抛 `AttributeError`。
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
    """提供可见导出列表，便于 `dir()` 与 IDE 补全。"""
    return list(__all__)
