"""
模块名称：字段范围规格

本模块定义输入控件的范围约束（最小值、最大值、步长与步长类型）。
主要功能包括：
- 使用 Pydantic 校验范围参数
- 支持 `int`/`float` 两种步长类型

关键组件：
- `RangeSpec`

设计背景：在 LFX 中统一 Slider 等控件的范围参数校验。
注意事项：`max` 必须大于 `min`，`step` 必须为正。
"""

from typing import Literal

from pydantic import BaseModel, field_validator


class RangeSpec(BaseModel):
    """范围规格模型。

    契约：
    - 输入：`min`/`max`/`step`/`step_type`
    - 输出：通过校验的范围规格实例
    - 副作用：无
    - 失败语义：校验失败抛 `ValueError`
    """

    step_type: Literal["int", "float"] = "float"
    min: float = -1.0
    max: float = 1.0
    step: float = 0.1

    @field_validator("max")
    @classmethod
    def max_must_be_greater_than_min(cls, v, values):
        """确保 `max` 大于 `min`。"""
        if "min" in values.data and v <= values.data["min"]:
            msg = "Max must be greater than min"
            raise ValueError(msg)
        return v

    @field_validator("step")
    @classmethod
    def step_must_be_positive(cls, v, values):
        """确保 `step` 为正且满足 `step_type` 约束。"""
        if v <= 0:
            msg = "Step must be positive"
            raise ValueError(msg)
        if values.data["step_type"] == "int" and isinstance(v, float) and not v.is_integer():
            msg = "When step_type is int, step must be an integer"
            raise ValueError(msg)
        return v

    @classmethod
    def set_step_type(cls, step_type: Literal["int", "float"], range_spec: "RangeSpec") -> "RangeSpec":
        """返回指定 `step_type` 的新 RangeSpec 实例。"""
        return cls(min=range_spec.min, max=range_spec.max, step=range_spec.step, step_type=step_type)
