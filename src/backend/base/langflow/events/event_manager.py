"""模块名称：事件管理兼容入口

模块目的：为旧路径 `langflow.events.event_manager` 提供向后兼容入口。
主要功能：将事件管理相关导出转发到 `lfx.events.event_manager`。
使用场景：历史代码仍使用旧包路径时的兼容访问。
关键组件：`EventManager`、`EventCallback`、`create_default_event_manager`
设计背景：事件系统迁移至 `lfx.events.event_manager`。
注意事项：本模块仅做导入转发，不包含实现逻辑。
"""

from lfx.events.event_manager import (
    EventCallback,
    EventManager,
    PartialEventCallback,
    create_default_event_manager,
    create_stream_tokens_event_manager,
)

__all__ = [
    "EventCallback",
    "EventManager",
    "PartialEventCallback",
    "create_default_event_manager",
    "create_stream_tokens_event_manager",
]
