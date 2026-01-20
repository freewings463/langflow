"""
模块名称：Tracing 日志结构

本模块定义 tracing 日志的 Pydantic 结构与序列化策略。
主要功能包括：
- 定义 `Log` 模型
- 对 `message` 字段做安全序列化

关键组件：
- `Log.serialize_message`

设计背景：日志内容类型复杂，需要统一序列化入口。
注意事项：序列化失败时回退到 `str()`。
"""

from pydantic import BaseModel, field_serializer
from pydantic_core import PydanticSerializationError

from langflow.schema.log import LoggableType
from langflow.serialization.serialization import serialize


class Log(BaseModel):
    name: str
    message: LoggableType
    type: str

    @field_serializer("message")
    def serialize_message(self, value):
        """序列化日志消息，失败时回退字符串。

        契约：返回可序列化值或字符串。
        失败语义：序列化异常时回退 `str(value)`。
        """
        try:
            return serialize(value)
        except UnicodeDecodeError:
            return str(value)  # Fallback to string representation
        except PydanticSerializationError:
            return str(value)  # Fallback to string for Pydantic errors
