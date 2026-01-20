"""
模块名称：输入校验器

本模块提供输入字段的基础校验函数与类型别名。
主要功能包括：
- 将常见字符串布尔值归一化为 `bool`
- 通过 Pydantic 的 `PlainValidator` 组合校验

关键组件：
- `validate_boolean`
- `CoalesceBool`

设计背景：表单输入可能来自字符串，需要统一归一化。
注意事项：无法识别的值将抛 `ValueError`。
"""

from typing import Annotated

from pydantic import PlainValidator


def validate_boolean(value: bool) -> bool:  # noqa: FBT001
    """将常见字符串布尔值转换为 `bool`。

    契约：
    - 输入：`bool` 或可识别的字符串布尔值
    - 输出：标准 `bool`
    - 副作用：无
    - 失败语义：无法识别时抛 `ValueError`
    """
    valid_trues = ["True", "true", "1", "yes"]
    valid_falses = ["False", "false", "0", "no"]
    if value in valid_trues:
        return True
    if value in valid_falses:
        return False
    if isinstance(value, bool):
        return value
    msg = "Value must be a boolean"
    raise ValueError(msg)


CoalesceBool = Annotated[bool, PlainValidator(validate_boolean)]
