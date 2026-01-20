"""
模块名称：`Message` 兼容导出

本模块转发 `lfx.schema.message` 中的消息模型，主要用于旧路径兼容。主要功能包括：
- 暴露 `Message` / `ErrorMessage` / `MessageResponse`
- 暴露 `ContentBlock` 与 `DefaultModel`

关键组件：
- Message
- ErrorMessage

设计背景：历史代码仍依赖 `langflow.schema.message`。
注意事项：通过重导出保持类型身份一致，行为由 `lfx` 实现决定。
"""

from lfx.schema.message import ContentBlock, DefaultModel, ErrorMessage, Message, MessageResponse

__all__ = ["ContentBlock", "DefaultModel", "ErrorMessage", "Message", "MessageResponse"]
