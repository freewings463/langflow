"""自定义工具函数。"""

from typing import Any


def format_type(type_: Any) -> str:
    """将类型对象格式化为可读名称。"""
    if type_ is str:
        type_ = "Text"
    elif hasattr(type_, "__name__"):
        type_ = type_.__name__
    elif hasattr(type_, "__class__"):
        type_ = type_.__class__.__name__
    else:
        type_ = str(type_)
    return type_
