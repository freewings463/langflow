"""
模块名称：template.field.prompt

本模块提供 prompt 字段的兼容性封装与默认配置。
主要功能包括：
- 定义 prompt 字段默认输入类型
- 提供兼容旧版本的默认字段模型

关键组件：
- DEFAULT_PROMPT_INTUT_TYPES：默认输入类型列表
- DefaultPromptField：默认 prompt 字段

设计背景：历史版本对 prompt 字段的期望不同，需要统一兼容入口。
注意事项：该模块仅负责默认值，不处理运行时校验逻辑。
"""

# 注意：提供 prompt 字段常量的向后兼容
from lfx.template.field.base import Input

# 默认的 prompt 输入类型
DEFAULT_PROMPT_INTUT_TYPES = ["Message"]


class DefaultPromptField(Input):
    """默认 prompt 字段（兼容旧版本）。"""

    field_type: str = "str"
    advanced: bool = False
    multiline: bool = True
    input_types: list[str] = DEFAULT_PROMPT_INTUT_TYPES
    value: str = ""  # 默认值置空，避免旧版本出现 None
