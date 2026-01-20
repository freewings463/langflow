"""
模块名称：内容块与类型联合

本模块定义可组合的内容块结构与多类型内容联合，主要用于消息展示与序列化。主要功能包括：
- 定义 `ContentType` 判别联合（基于 `type`）
- 提供 `ContentBlock` 统一封装结构

关键组件：
- ContentType：带鉴别器的内容联合
- ContentBlock：内容块模型

设计背景：统一消息内容表达，支持文本/媒体/工具等多形态。
注意事项：`contents` 允许单项输入并会被标准化为列表。
"""

from typing import Annotated

from pydantic import BaseModel, Discriminator, Field, Tag, field_serializer, field_validator
from typing_extensions import TypedDict

from .content_types import CodeContent, ErrorContent, JSONContent, MediaContent, TextContent, ToolContent


def _get_type(d: dict | BaseModel) -> str | None:
    """从对象或字典中提取 `type` 字段。

    契约：返回 `type` 字段值或 `None`。
    """
    if isinstance(d, dict):
        return d.get("type")
    return getattr(d, "type", None)


ContentType = Annotated[
    Annotated[ToolContent, Tag("tool_use")]
    | Annotated[ErrorContent, Tag("error")]
    | Annotated[TextContent, Tag("text")]
    | Annotated[MediaContent, Tag("media")]
    | Annotated[CodeContent, Tag("code")]
    | Annotated[JSONContent, Tag("json")],
    Discriminator(_get_type),
]


class ContentBlock(BaseModel):
    """内容块模型。

    契约：`contents` 必须为 `ContentType` 列表；`allow_markdown` 默认开启。
    副作用：初始化时会标记含默认值的字段为“已设置”，保证序列化稳定性。
    失败语义：`contents` 传入字典时抛 `TypeError`。
    """

    title: str
    contents: list[ContentType]
    allow_markdown: bool = Field(default=True)
    media_url: list[str] | None = None

    def __init__(self, **data) -> None:
        """初始化并标记默认字段为显式设置。

        关键路径（三步）：
        1) 调用 `BaseModel` 初始化。
        2) 读取 `__pydantic_core_schema__` 获取字段默认值。
        3) 将含默认值字段加入 `model_fields_set`。
        """
        super().__init__(**data)
        schema_dict = self.__pydantic_core_schema__["schema"]
        if "fields" in schema_dict:
            fields = schema_dict["fields"]
        elif "schema" in schema_dict:
            fields = schema_dict["schema"]["fields"]
        fields_with_default = (f for f, d in fields.items() if "default" in d["schema"])
        self.model_fields_set.update(fields_with_default)

    @field_validator("contents", mode="before")
    @classmethod
    def validate_contents(cls, v) -> list[ContentType]:
        """规范化 `contents` 为列表。

        契约：`BaseModel` 转为单元素列表；列表原样返回。
        失败语义：输入为 `dict` 时抛 `TypeError`。
        """
        if isinstance(v, dict):
            msg = "Contents must be a list of ContentTypes"
            raise TypeError(msg)
        return [v] if isinstance(v, BaseModel) else v

    @field_serializer("contents")
    def serialize_contents(self, value) -> list[dict]:
        """序列化 `contents` 为字典列表。

        契约：返回每个内容的 `model_dump` 结果。
        """
        return [v.model_dump() for v in value]


class ContentBlockDict(TypedDict):
    """内容块字典结构。

    契约：与 `ContentBlock.model_dump()` 输出保持字段一致。
    """

    title: str
    contents: list[dict]
    allow_markdown: bool
    media_url: list[str] | None
