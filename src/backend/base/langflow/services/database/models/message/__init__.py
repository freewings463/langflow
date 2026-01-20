"""
模块名称：消息模型导出

本模块导出消息相关模型。
主要功能包括：统一 `Message` 创建/读取/更新模型导出。

关键组件：`MessageCreate` / `MessageRead` / `MessageTable` / `MessageUpdate`
设计背景：简化上层导入路径。
使用场景：服务层与 API 序列化。
注意事项：更新操作在 `crud.py` 中实现。
"""

from .model import MessageCreate, MessageRead, MessageTable, MessageUpdate

__all__ = ["MessageCreate", "MessageRead", "MessageTable", "MessageUpdate"]
