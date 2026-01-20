"""
模块名称：DataStax 组件包入口

本模块用于集中管理 DataStax/AstraDB 相关组件的延迟导入，降低可选依赖对启动的影响。主要功能包括：
- 作为 `lfx.components.datastax` 的包级入口
- 按需加载 AstraDB 与 Assistants 相关组件

关键组件：
- AstraDB 相关组件与 Assistants 相关组件（见 `__all__`）

设计背景：组件依赖多且部分可选，需要延迟导入避免启动失败。
使用场景：上层按需引用 DataStax 组件。
注意事项：访问未知属性将抛 `AttributeError`。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lfx.components._importing import import_mod

if TYPE_CHECKING:
    from .astradb_assistant_manager import AstraAssistantManager
    from .astradb_chatmemory import AstraDBChatMemory
    from .astradb_cql import AstraDBCQLToolComponent
    from .astradb_graph import AstraDBGraphVectorStoreComponent
    from .astradb_tool import AstraDBToolComponent
    from .astradb_vectorize import AstraVectorizeComponent
    from .astradb_vectorstore import AstraDBVectorStoreComponent
    from .create_assistant import AssistantsCreateAssistant
    from .create_thread import AssistantsCreateThread
    from .dotenv import Dotenv
    from .get_assistant import AssistantsGetAssistantName
    from .getenvvar import GetEnvVar
    from .graph_rag import GraphRAGComponent
    from .list_assistants import AssistantsListAssistants
    from .run import AssistantsRun

_dynamic_imports = {
    "AssistantsCreateAssistant": "create_assistant",
    "AssistantsCreateThread": "create_thread",
    "AssistantsGetAssistantName": "get_assistant",
    "AssistantsListAssistants": "list_assistants",
    "AssistantsRun": "run",
    "AstraAssistantManager": "astradb_assistant_manager",
    "AstraDBCQLToolComponent": "astradb_cql",
    "AstraDBChatMemory": "astradb_chatmemory",
    "AstraDBGraphVectorStoreComponent": "astradb_graph",
    "AstraDBToolComponent": "astradb_tool",
    "AstraDBVectorStoreComponent": "astradb_vectorstore",
    "AstraVectorizeComponent": "astradb_vectorize",
    "Dotenv": "dotenv",
    "GetEnvVar": "getenvvar",
    "GraphRAGComponent": "graph_rag",
}

__all__ = [
    "AssistantsCreateAssistant",
    "AssistantsCreateThread",
    "AssistantsGetAssistantName",
    "AssistantsListAssistants",
    "AssistantsRun",
    "AstraAssistantManager",
    "AstraDBCQLToolComponent",
    "AstraDBChatMemory",
    "AstraDBGraphVectorStoreComponent",
    "AstraDBToolComponent",
    "AstraDBVectorStoreComponent",
    "AstraVectorizeComponent",
    "Dotenv",
    "GetEnvVar",
    "GraphRAGComponent",
]


def __getattr__(attr_name: str) -> Any:
    """按需延迟导入 DataStax 组件

    契约：输入属性名并返回对应组件对象；副作用：缓存到 `globals()`；
    失败语义：属性未知或导入失败抛 `AttributeError`。
    关键路径：1) 校验属性名 2) 动态导入 3) 缓存并返回。
    决策：采用延迟导入而非模块加载时全部导入。
    问题：AstraDB 相关依赖可选且体量大，提前导入易失败。
    方案：首次访问时加载并缓存。
    代价：首次访问存在额外导入开销。
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
