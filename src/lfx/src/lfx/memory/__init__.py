"""
模块名称：Memory 动态加载入口

本模块在运行时选择 Langflow 的完整 memory 实现或 lfx 的 stub 实现。
主要功能：
- 根据环境自动切换 memory 实现；
- 统一导出 memory 相关函数接口。

设计背景：支持独立运行与完整 Langflow 环境的兼容。
注意事项：若 Langflow 不可用则回退到 stub，实现功能有限。
"""

from lfx.utils.langflow_utils import has_langflow_memory

# 注意：优先使用 Langflow 完整实现，失败回退到 lfx stub。
if has_langflow_memory():
    try:
        # 实现：加载 Langflow 完整 memory 接口。
        from langflow.memory import (
            aadd_messages,
            aadd_messagetables,
            add_messages,
            adelete_messages,
            aget_messages,
            astore_message,
            aupdate_messages,
            delete_message,
            delete_messages,
            get_messages,
            store_message,
        )
    except ImportError:
        # 注意：Langflow 导入失败时回退到 lfx stub。
        from lfx.memory.stubs import (
            aadd_messages,
            aadd_messagetables,
            add_messages,
            adelete_messages,
            aget_messages,
            astore_message,
            aupdate_messages,
            delete_message,
            delete_messages,
            get_messages,
            store_message,
        )
else:
    # 注意：独立环境直接使用 lfx stub。
    from lfx.memory.stubs import (
        aadd_messages,
        aadd_messagetables,
        add_messages,
        adelete_messages,
        aget_messages,
        astore_message,
        aupdate_messages,
        delete_message,
        delete_messages,
        get_messages,
        store_message,
    )

# 注意：统一导出可用的 memory 接口。
__all__ = [
    "aadd_messages",
    "aadd_messagetables",
    "add_messages",
    "adelete_messages",
    "aget_messages",
    "astore_message",
    "aupdate_messages",
    "delete_message",
    "delete_messages",
    "get_messages",
    "store_message",
]
