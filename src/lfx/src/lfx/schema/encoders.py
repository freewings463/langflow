"""自定义编码器集合。"""

from collections.abc import Callable
from datetime import datetime


def encode_callable(obj: Callable):
    """编码可调用对象为字符串表示。

    契约：
    - 输入：可调用对象
    - 输出：对象名称或字符串表示
    - 副作用：无
    - 失败语义：无
    """
    return obj.__name__ if hasattr(obj, "__name__") else str(obj)


def encode_datetime(obj: datetime):
    """编码日期时间为字符串表示。

    契约：
    - 输入：datetime 对象
    - 输出：格式化的日期时间字符串
    - 副作用：无
    - 失败语义：无
    """
    return obj.strftime("%Y-%m-%d %H:%M:%S %Z")


CUSTOM_ENCODERS = {Callable: encode_callable, datetime: encode_datetime}
