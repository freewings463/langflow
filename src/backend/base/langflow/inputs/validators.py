"""
模块名称：输入校验器兼容层

本模块转发 `lfx.inputs.validators` 的校验器，主要用于保持旧导入路径可用。
主要功能包括：
- 暴露 `CoalesceBool` 与 `validate_boolean`。

关键组件：`CoalesceBool`、`validate_boolean`。
设计背景：校验器实现迁移至 `lfx`，旧路径需继续工作。
使用场景：历史代码从 `langflow.inputs.validators` 引用校验逻辑。
注意事项：所有校验行为以 `lfx` 实现为准。
"""

from lfx.inputs.validators import CoalesceBool, validate_boolean

__all__ = ["CoalesceBool", "validate_boolean"]
