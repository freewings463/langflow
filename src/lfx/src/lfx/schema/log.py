"""日志相关 schema 与类型。"""

from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, field_serializer
from pydantic_core import PydanticSerializationError
from typing_extensions import Protocol

from lfx.schema.message import ContentBlock, Message
from lfx.serialization.serialization import serialize

# 简化版 LoggableType（移除 PlaygroundEvent 依赖）
LoggableType: TypeAlias = str | dict | list | int | float | bool | BaseModel | None


class LogFunctionType(Protocol):
    """日志函数协议。

    契约：
    - 输入：可记录类型的消息和可选名称
    - 输出：无
    - 副作用：记录日志
    - 失败语义：无
    """

    def __call__(self, message: LoggableType | list[LoggableType], *, name: str | None = None) -> None: ...


class SendMessageFunctionType(Protocol):
    """发送消息函数协议。

    契约：
    - 输入：消息参数（多种可选参数）
    - 输出：创建的消息对象
    - 副作用：发送消息
    - 失败语义：无
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
        skip_db_update: bool = False,
    ) -> Message: ...


class OnTokenFunctionType(Protocol):
    """Token 回调函数协议。

    契约：
    - 输入：包含令牌数据的字典
    - 输出：无
    - 副作用：处理令牌数据
    - 失败语义：无
    """

    def __call__(self, data: dict[str, Any]) -> None: ...


class Log(BaseModel):
    """日志模型（支持序列化）。

    关键路径（三步）：
    1) 定义日志的基本属性（名称、消息、类型）
    2) 提供消息字段的序列化方法
    3) 处理序列化错误并提供备选方案

    异常流：序列化失败时使用备选方案。
    性能瓶颈：序列化大型对象。
    排障入口：无特定日志输出。
    """

    name: str
    message: LoggableType
    type: str

    @field_serializer("message")
    def serialize_message(self, value):
        """序列化消息字段并提供降级处理。

        契约：
        - 输入：待序列化的消息值
        - 输出：序列化后的值
        - 副作用：无
        - 失败语义：序列化失败时返回字符串表示
        """
        try:
            return serialize(value)
        except UnicodeDecodeError:
            return str(value)  # 回退为字符串
        except PydanticSerializationError:
            return str(value)  # Pydantic 异常时回退为字符串
