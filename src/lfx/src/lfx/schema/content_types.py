"""内容类型 schema。

本模块定义文本/媒体/错误/工具等内容类型的数据结构。
"""

from typing import Any, Literal

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, model_serializer
from typing_extensions import TypedDict

from lfx.schema.encoders import CUSTOM_ENCODERS


class HeaderDict(TypedDict, total=False):
    """头部信息字典。

    契约：
    - 输入：可选的标题和图标参数
    - 输出：HeaderDict实例
    - 副作用：无
    - 失败语义：无
    """
    title: str | None
    icon: str | None


class BaseContent(BaseModel):
    """所有内容类型的基类。

    关键路径（三步）：
    1) 定义基本内容属性（类型、持续时间、头部）
    2) 提供字典序列化和反序列化方法
    3) 自定义序列化行为

    异常流：序列化失败时回退到默认序列化方法。
    性能瓶颈：自定义序列化过程。
    排障入口：logger.debug 输出序列化错误详情。
    """

    type: str = Field(..., description="Type of the content")
    duration: int | None = None
    header: HeaderDict | None = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式。

        契约：
        - 输入：无
        - 输出：模型数据的字典表示
        - 副作用：无
        - 失败语义：无
        """
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BaseContent":
        """从字典创建实例。

        契约：
        - 输入：包含模型数据的字典
        - 输出：BaseContent子类实例
        - 副作用：无
        - 失败语义：数据验证失败时抛出异常
        """
        return cls(**data)

    @model_serializer(mode="wrap")
    def serialize_model(self, nxt) -> dict[str, Any]:
        """自定义模型序列化。

        契约：
        - 输入：序列化函数
        - 输出：序列化后的字典
        - 副作用：使用自定义编码器
        - 失败语义：失败时回退到默认序列化方法
        """
        try:
            dump = nxt(self)
            return jsonable_encoder(dump, custom_encoder=CUSTOM_ENCODERS)
        except Exception:  # noqa: BLE001
            return nxt(self)


class ErrorContent(BaseContent):
    """错误消息内容类型。

    契约：
    - 输入：错误相关信息
    - 输出：ErrorContent实例
    - 副作用：无
    - 失败语义：无
    """

    type: Literal["error"] = Field(default="error")
    component: str | None = None
    field: str | None = None
    reason: str | None = None
    solution: str | None = None
    traceback: str | None = None


class TextContent(BaseContent):
    """简单文本内容类型。

    契约：
    - 输入：文本内容及相关信息
    - 输出：TextContent实例
    - 副作用：无
    - 失败语义：无
    """

    type: Literal["text"] = Field(default="text")
    text: str
    duration: int | None = None


class MediaContent(BaseContent):
    """媒体内容类型。

    契约：
    - 输入：媒体URL及可选说明
    - 输出：MediaContent实例
    - 副作用：无
    - 失败语义：无
    """

    type: Literal["media"] = Field(default="media")
    urls: list[str]
    caption: str | None = None


class JSONContent(BaseContent):
    """JSON内容类型。

    契约：
    - 输入：JSON数据
    - 输出：JSONContent实例
    - 副作用：无
    - 失败语义：无
    """

    type: Literal["json"] = Field(default="json")
    data: dict[str, Any]


class CodeContent(BaseContent):
    """代码片段内容类型。

    契约：
    - 输入：代码内容及语言信息
    - 输出：CodeContent实例
    - 副作用：无
    - 失败语义：无
    """

    type: Literal["code"] = Field(default="code")
    code: str
    language: str
    title: str | None = None


class ToolContent(BaseContent):
    """工具启动内容类型。

    契约：
    - 输入：工具相关信息
    - 输出：ToolContent实例
    - 副作用：无
    - 失败语义：无
    """

    model_config = ConfigDict(populate_by_name=True)

    type: Literal["tool_use"] = Field(default="tool_use")
    name: str | None = None
    tool_input: dict[str, Any] = Field(default_factory=dict, alias="input")
    output: Any | None = None
    error: Any | None = None
    duration: int | None = None
