"""
模块名称：内容类型定义

本模块定义消息内容的具体类型与序列化行为，主要用于前端展示与日志记录。主要功能包括：
- 定义各类内容模型（文本/媒体/代码/工具等）
- 提供统一序列化逻辑与自定义编码器支持

关键组件：
- `BaseContent`：内容基类
- `TextContent` / `MediaContent` / `CodeContent` 等具体类型

设计背景：消息内容多样化，需要统一的数据结构与可序列化输出。
注意事项：序列化失败时会回退到默认 `model_dump` 结果。
"""

from typing import Any, Literal

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, model_serializer
from typing_extensions import TypedDict

from langflow.schema.encoders import CUSTOM_ENCODERS


class HeaderDict(TypedDict, total=False):
    """内容头部字段结构。"""

    title: str | None
    icon: str | None


class BaseContent(BaseModel):
    """内容类型基类。

    契约：包含 `type` 字段并支持 `to_dict`/`from_dict` 转换。
    副作用：`model_serializer` 会使用自定义编码器输出 `JSON` 友好结构。
    失败语义：编码失败时回退 `model_dump` 原始结果。
    """

    type: str = Field(..., description="Type of the content")
    duration: int | None = None
    header: HeaderDict | None = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """将模型转换为字典。

        契约：返回 `model_dump` 结果。
        """
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BaseContent":
        """从字典构造内容模型。

        失败语义：字段不合法时抛 `ValidationError`。
        """
        return cls(**data)

    @model_serializer(mode="wrap")
    def serialize_model(self, nxt) -> dict[str, Any]:
        """统一序列化入口，支持自定义编码器。

        契约：优先使用 `CUSTOM_ENCODERS`；失败时回退原始序列化。
        """
        try:
            dump = nxt(self)
            return jsonable_encoder(dump, custom_encoder=CUSTOM_ENCODERS)
        except Exception:  # noqa: BLE001
            return nxt(self)


class ErrorContent(BaseContent):
    """错误内容类型。

    契约：`type` 固定为 `error`，可携带 `reason`/`solution`/`traceback`。
    失败语义：字段校验失败抛 `ValidationError`。
    """

    type: Literal["error"] = Field(default="error")
    component: str | None = None
    field: str | None = None
    reason: str | None = None
    solution: str | None = None
    traceback: str | None = None


class TextContent(BaseContent):
    """文本内容类型。

    契约：`text` 为必填文本；`duration` 可选。
    失败语义：字段校验失败抛 `ValidationError`。
    """

    type: Literal["text"] = Field(default="text")
    text: str
    duration: int | None = None


class MediaContent(BaseContent):
    """媒体内容类型。

    契约：`urls` 为必填列表；`caption` 可选。
    失败语义：字段校验失败抛 `ValidationError`。
    """

    type: Literal["media"] = Field(default="media")
    urls: list[str]
    caption: str | None = None


class JSONContent(BaseContent):
    """`JSON` 内容类型。

    契约：`data` 必须为字典结构。
    失败语义：字段校验失败抛 `ValidationError`。
    """

    type: Literal["json"] = Field(default="json")
    data: dict[str, Any]


class CodeContent(BaseContent):
    """代码片段内容类型。

    契约：`code` 与 `language` 为必填；`title` 可选。
    失败语义：字段校验失败抛 `ValidationError`。
    """

    type: Literal["code"] = Field(default="code")
    code: str
    language: str
    title: str | None = None


class ToolContent(BaseContent):
    """工具调用内容类型。

    契约：`tool_input` 使用别名 `input`；`output`/`error` 可选。
    失败语义：字段校验失败抛 `ValidationError`。
    """

    model_config = ConfigDict(populate_by_name=True)

    type: Literal["tool_use"] = Field(default="tool_use")
    name: str | None = None
    tool_input: dict[str, Any] = Field(default_factory=dict, alias="input")
    output: Any | None = None
    error: Any | None = None
    duration: int | None = None
