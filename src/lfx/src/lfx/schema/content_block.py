"""内容块 schema。

本模块定义可包含多类型内容的块结构与联合类型。
"""

from typing import Annotated

from pydantic import BaseModel, Discriminator, Field, Tag, field_serializer, field_validator
from typing_extensions import TypedDict

from .content_types import CodeContent, ErrorContent, JSONContent, MediaContent, TextContent, ToolContent


def _get_type(d: dict | BaseModel) -> str | None:
    """获取内容的类型。

    契约：
    - 输入：字典或BaseModel对象
    - 输出：类型字符串或None
    - 副作用：无
    - 失败语义：无
    """
    if isinstance(d, dict):
        return d.get("type")
    return getattr(d, "type", None)


# 创建所有内容类型的联合类型
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
    """可包含不同类型内容的块。

    关键路径（三步）：
    1) 初始化内容块并设置默认字段
    2) 验证内容列表的有效性
    3) 序列化内容为字典格式

    异常流：验证失败时抛出TypeError。
    性能瓶颈：内容序列化过程。
    排障入口：无特定日志输出。
    """

    title: str
    contents: list[ContentType]
    allow_markdown: bool = Field(default=True)
    media_url: list[str] | None = None

    def __init__(self, **data) -> None:
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
        """验证内容列表。

        契约：
        - 输入：待验证的内容数据
        - 输出：验证后的内容列表
        - 副作用：可能抛出类型错误
        - 失败语义：输入为字典时抛出TypeError
        """
        if isinstance(v, dict):
            msg = "Contents must be a list of ContentTypes"
            raise TypeError(msg)
        return [v] if isinstance(v, BaseModel) else v

    @field_serializer("contents")
    def serialize_contents(self, value) -> list[dict]:
        """序列化内容列表为字典格式。

        契约：
        - 输入：内容对象列表
        - 输出：字典格式的内容列表
        - 副作用：无
        - 失败语义：无
        """
        return [v.model_dump() for v in value]


class ContentBlockDict(TypedDict):
    """ContentBlock的字典表示。

    契约：
    - 输入：字典类型的构造参数
    - 输出：ContentBlock字典实例
    - 副作用：无
    - 失败语义：无
    """
    title: str
    contents: list[dict]
    allow_markdown: bool
    media_url: list[str] | None
