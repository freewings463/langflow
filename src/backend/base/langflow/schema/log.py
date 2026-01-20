"""
模块名称：日志与回调协议定义

本模块定义日志消息类型与回调协议，主要用于组件/前端日志输出的类型约束。主要功能包括：
- 统一可记录的日志类型集合
- 约束日志发送与 token 回调签名

关键组件：
- `LoggableType`：可记录类型别名
- `LogFunctionType` / `SendMessageFunctionType` / `OnTokenFunctionType`

设计背景：统一日志回调形态，降低调用方与实现方的协议歧义。
注意事项：协议仅做类型约束，不提供实现。
"""

from typing import Any, Literal, TypeAlias

from pydantic import BaseModel
from typing_extensions import Protocol

from langflow.schema.message import ContentBlock, Message
from langflow.schema.playground_events import PlaygroundEvent

LoggableType: TypeAlias = str | dict | list | int | float | bool | BaseModel | PlaygroundEvent | None


class LogFunctionType(Protocol):
    """日志函数协议。

    契约：接收单条或多条 `LoggableType`，可选 `name` 标识来源。
    失败语义：由实现方决定；协议不约束异常行为。
    """

    def __call__(self, message: LoggableType | list[LoggableType], *, name: str | None = None) -> None: ...


class SendMessageFunctionType(Protocol):
    """消息发送函数协议（异步）。

    契约：允许通过文本或 `Message` 构造发送，返回 `Message`。
    副作用：通常会产生网络/事件发送；具体由实现决定。
    失败语义：由实现方抛出；调用方需处理异常。
    """

    async def __call__(
        self,
        message: Message | None = None,
        text: str | None = None,
        background_color: str | None = None,
        text_color: str | None = None,
        icon: str | None = None,
        content_blocks: list[ContentBlock] | None = None,
        format_type: Literal["default", "error", "warning", "info"] = "default",
        id_: str | None = None,
        *,
        allow_markdown: bool = True,
    ) -> Message: ...


class OnTokenFunctionType(Protocol):
    """`Token` 回调函数协议。"""

    def __call__(self, data: dict[str, Any]) -> None: ...
