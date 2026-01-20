"""
模块名称：自定义格式化工具

本模块提供类型显示名称的格式化能力。
主要功能包括：
- 将 Python 类型或对象转换为可读字符串
- 对 `str` 类型做 UI 友好映射

关键组件：
- `format_type`

设计背景：前端展示类型信息时需要更易读的名称。
注意事项：未知对象将回退到 `str()` 表示。
"""

from typing import Any


def format_type(type_: Any) -> str:
    """格式化类型显示名称。

    契约：输入类型或实例，返回字符串名称。
    失败语义：不抛异常，无法识别时回退到 `str(type_)`。

    决策：将 `str` 显示为 `Text`
    问题：直接展示 `str` 对非技术用户不友好
    方案：统一映射为 `Text`
    代价：类型展示与真实 Python 名称不一致
    重评：当 UI 有国际化词表时改为词表映射
    """
    if type_ is str:
        type_ = "Text"
    elif hasattr(type_, "__name__"):
        type_ = type_.__name__
    elif hasattr(type_, "__class__"):
        type_ = type_.__class__.__name__
    else:
        type_ = str(type_)
    return type_
