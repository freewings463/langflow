"""
模块名称：自定义序列化编码器

本模块提供常用类型的自定义编码函数，主要用于 `JSON` 序列化。主要功能包括：
- 将可调用对象编码为名称
- 将时间对象编码为统一字符串

关键组件：
- encode_callable / encode_datetime
- CUSTOM_ENCODERS

设计背景：`Pydantic`/`JSON` 序列化需对不可直接编码类型提供兜底。
注意事项：时间格式固定为 `YYYY-MM-DD HH:MM:SS TZ`。
"""

from collections.abc import Callable
from datetime import datetime


def encode_callable(obj: Callable):
    """将可调用对象编码为可读名称。

    契约：优先返回 `__name__`，否则返回 `str(obj)`。
    """
    return obj.__name__ if hasattr(obj, "__name__") else str(obj)


def encode_datetime(obj: datetime):
    """将时间对象编码为统一字符串格式。

    契约：输出格式为 `YYYY-MM-DD HH:MM:SS TZ`。
    """
    return obj.strftime("%Y-%m-%d %H:%M:%S %Z")


CUSTOM_ENCODERS = {Callable: encode_callable, datetime: encode_datetime}
