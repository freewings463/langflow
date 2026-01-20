"""
模块名称：`Playground` 事件模型与工厂

本模块定义 `Playground` 事件模型并提供创建工厂，主要用于消息流与事件广播。主要功能包括：
- 定义消息/错误/警告/信息/`Token` 事件结构
- 提供创建事件的工厂函数与类型分发

关键组件：
- `PlaygroundEvent` / `MessageEvent` / `ErrorEvent` / `TokenEvent`
- create_message / create_error / create_event_by_type

设计背景：统一事件结构，便于前端渲染与后端日志。
注意事项：`timestamp` 使用 `UTC` 字符串；`id` 支持 `UUID` 与字符串。
"""

import inspect
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

from lfx.schema.content_block import ContentBlock
from lfx.schema.content_types import ErrorContent
from lfx.schema.properties import Properties
from lfx.schema.validators import timestamp_to_str_validator
from lfx.utils.constants import MESSAGE_SENDER_USER
from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator


class PlaygroundEvent(BaseModel):
    """`Playground` 事件基础模型。

    契约：允许额外字段；`timestamp` 统一为字符串格式。
    副作用：无。
    失败语义：字段校验失败抛 `ValidationError`。
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)
    properties: Properties | None = Field(default=None)
    sender_name: str | None = Field(default=None)
    content_blocks: list[ContentBlock] | None = Field(default=None)
    format_type: Literal["default", "error", "warning", "info"] = Field(default="default")
    files: list[str] | None = Field(default=None)
    text: str | None = Field(default=None)
    timestamp: Annotated[str, timestamp_to_str_validator] = Field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z")
    )
    id_: UUID | str | None = Field(default=None, alias="id")

    @field_serializer("timestamp")
    @classmethod
    def serialize_timestamp(cls, v: str) -> str:
        """保持时间戳原样输出。"""
        return v

    @field_validator("id_")
    @classmethod
    def validate_id(cls, v: UUID | str | None) -> str | None:
        """将 `UUID` 标准化为字符串。"""
        if isinstance(v, UUID):
            return str(v)
        return v


class MessageEvent(PlaygroundEvent):
    """消息事件模型。

    契约：`category` 标识消息类别；`sender` 默认用户。
    失败语义：字段校验失败抛 `ValidationError`。
    """

    category: Literal["message", "error", "warning", "info"] = "message"
    format_type: Literal["default", "error", "warning", "info"] = Field(default="default")
    session_id: str | None = Field(default=None)
    error: bool = Field(default=False)
    edit: bool = Field(default=False)
    flow_id: UUID | str | None = Field(default=None)
    sender: str = Field(default=MESSAGE_SENDER_USER)
    sender_name: str = Field(default="User")

    @field_validator("flow_id")
    @classmethod
    def validate_flow_id(cls, v: UUID | str | None) -> str | None:
        """将 `flow_id` 标准化为字符串。"""
        if isinstance(v, UUID):
            return str(v)
        return v


class ErrorEvent(MessageEvent):
    """错误事件模型。

    契约：默认红色背景并关闭 `allow_markdown`。
    失败语义：字段校验失败抛 `ValidationError`。
    """

    background_color: str = Field(default="#FF0000")
    text_color: str = Field(default="#FFFFFF")
    format_type: Literal["default", "error", "warning", "info"] = Field(default="error")
    allow_markdown: bool = Field(default=False)
    category: Literal["error"] = "error"


class WarningEvent(PlaygroundEvent):
    """警告事件模型。

    契约：默认橙色背景，`format_type=warning`。
    失败语义：字段校验失败抛 `ValidationError`。
    """

    background_color: str = Field(default="#FFA500")
    text_color: str = Field(default="#000000")
    format_type: Literal["default", "error", "warning", "info"] = Field(default="warning")


class InfoEvent(PlaygroundEvent):
    """信息事件模型。

    契约：默认蓝色背景，`format_type=info`。
    失败语义：字段校验失败抛 `ValidationError`。
    """

    background_color: str = Field(default="#0000FF")
    text_color: str = Field(default="#FFFFFF")
    format_type: Literal["default", "error", "warning", "info"] = Field(default="info")


class TokenEvent(BaseModel):
    """流式 `Token` 事件模型。

    契约：`chunk` 为必填文本片段；`id` 支持 `UUID` 或字符串。
    失败语义：字段校验失败抛 `ValidationError`。
    """

    chunk: str = Field(...)
    id: UUID | str | None = Field(alias="id")
    timestamp: Annotated[str, timestamp_to_str_validator] = Field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z")
    )


def create_message(
    text: str,
    category: Literal["message", "error", "warning", "info"] = "message",
    properties: dict | None = None,
    content_blocks: list[ContentBlock] | None = None,
    sender_name: str | None = None,
    files: list[str] | None = None,
    timestamp: str | None = None,
    format_type: Literal["default", "error", "warning", "info"] = "default",
    sender: str | None = None,
    session_id: str | None = None,
    id: UUID | str | None = None,  # noqa: A002
    flow_id: UUID | str | None = None,
    *,
    error: bool = False,
    edit: bool = False,
) -> MessageEvent:
    """创建消息事件。

    契约：返回 `MessageEvent`，不修改输入参数。
    失败语义：字段校验失败抛 `ValidationError`。
    """
    return MessageEvent(
        text=text,
        properties=properties,
        category=category,
        content_blocks=content_blocks,
        sender_name=sender_name,
        files=files,
        timestamp=timestamp,
        format_type=format_type,
        sender=sender,
        id=id,
        session_id=session_id,
        error=error,
        edit=edit,
        flow_id=flow_id,
    )


def create_error(
    text: str,
    properties: dict | None = None,
    traceback: str | None = None,
    title: str = "Error",
    timestamp: str | None = None,
    id: UUID | str | None = None,  # noqa: A002
    flow_id: UUID | str | None = None,
    session_id: str | None = None,
    content_blocks: list[ContentBlock] | None = None,
) -> ErrorEvent:
    """创建错误事件，可附带 traceback 内容块。

    契约：`traceback` 存在时会追加 `ErrorContent` 内容块。
    失败语义：字段校验失败抛 `ValidationError`。
    """
    if traceback:
        content_blocks = content_blocks or []
        content_blocks += [ContentBlock(title=title, contents=[ErrorContent(type="error", traceback=traceback)])]
    return ErrorEvent(
        text=text,
        properties=properties,
        content_blocks=content_blocks,
        timestamp=timestamp,
        id=id,
        flow_id=flow_id,
        session_id=session_id,
    )


def create_warning(message: str) -> WarningEvent:
    """创建警告事件。

    契约：输入为纯文本，返回 `WarningEvent` 且 `format_type=warning`。
    失败语义：字段校验失败抛 `ValidationError`。
    """
    return WarningEvent(text=message)


def create_info(message: str) -> InfoEvent:
    """创建信息事件。

    契约：输入为纯文本，返回 `InfoEvent` 且 `format_type=info`。
    失败语义：字段校验失败抛 `ValidationError`。
    """
    return InfoEvent(text=message)


def create_token(chunk: str, id: str) -> TokenEvent:  # noqa: A002
    """创建流式 `Token` 事件。

    契约：`chunk` 为本次输出片段；`id` 用于关联流式会话。
    失败语义：字段校验失败抛 `ValidationError`。
    """
    return TokenEvent(
        chunk=chunk,
        id=id,
    )


_EVENT_CREATORS: dict[str, tuple[Callable, inspect.Signature]] = {
    "message": (create_message, inspect.signature(create_message)),
    "error": (create_error, inspect.signature(create_error)),
    "warning": (create_warning, inspect.signature(create_warning)),
    "info": (create_info, inspect.signature(create_info)),
    "token": (create_token, inspect.signature(create_token)),
}


def create_event_by_type(event_type: str, **kwargs) -> PlaygroundEvent | dict:
    """按类型创建事件或回退返回原参数。

    契约：识别到的类型返回对应事件模型；未知类型返回原始 `kwargs`。
    关键路径（三步）：
    1) 校验 `event_type` 是否存在于注册表。
    2) 过滤不在函数签名内的参数。
    3) 调用对应工厂函数生成事件。
    失败语义：`event_type` 关键缺失抛 `ValueError`。
    """
    if event_type not in _EVENT_CREATORS:
        return kwargs
    try:
        creator_func, signature = _EVENT_CREATORS[event_type]
    except KeyError as e:
        msg = f"Invalid event type: {event_type}"
        raise ValueError(msg) from e
    valid_params = {k: v for k, v in kwargs.items() if k in signature.parameters}
    return creator_func(**valid_params)
