"""
模块名称：消息属性模型

本模块定义消息展示与状态相关属性，主要用于前端渲染控制。主要功能包括：
- 统一消息来源与展示样式字段
- 处理 `source` 的输入归一化与序列化

关键组件：
- Source：消息来源信息
- Properties：消息属性集合

设计背景：消息展示需要携带颜色、来源与状态等附加信息。
注意事项：`source` 支持字符串快捷输入，会被转换为 `Source`。
"""

from typing import Literal

from pydantic import BaseModel, Field, field_serializer, field_validator


class Source(BaseModel):
    """消息来源信息。

    契约：字段均可为空，用于记录模型来源与展示名称。
    失败语义：字段校验失败抛 `ValidationError`。
    """

    id: str | None = Field(default=None, description="The id of the source component.")
    display_name: str | None = Field(default=None, description="The display name of the source component.")
    source: str | None = Field(
        default=None,
        description="The source of the message. Normally used to display the model name (e.g. 'gpt-4o')",
    )


class Properties(BaseModel):
    """消息展示属性。

    契约：包含颜色、来源与状态等字段；`source` 会被归一化为 `Source`。
    副作用：无。
    失败语义：字段校验失败抛 `ValidationError`。
    """

    text_color: str | None = None
    background_color: str | None = None
    edited: bool = False
    source: Source = Field(default_factory=Source)
    icon: str | None = None
    allow_markdown: bool = False
    positive_feedback: bool | None = None
    state: Literal["partial", "complete"] = "complete"
    targets: list = []

    @field_validator("source", mode="before")
    @classmethod
    def validate_source(cls, v):
        """归一化 `source` 字段输入。"""
        if isinstance(v, str):
            return Source(id=v, display_name=v, source=v)
        if v is None:
            return Source()
        return v

    @field_serializer("source")
    def serialize_source(self, value):
        """序列化 `source` 为字典。"""
        if isinstance(value, Source):
            return value.model_dump()
        return value
