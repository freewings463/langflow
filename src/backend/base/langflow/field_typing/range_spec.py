"""
模块名称：`RangeSpec` 兼容导出

本模块提供 `RangeSpec` 的兼容导出入口，主要用于旧路径引用。主要功能包括：
- 从 `lfx.field_typing.range_spec` 转发 `RangeSpec`

关键组件：
- `RangeSpec`：字段范围描述类型

设计背景：历史代码使用 `langflow.field_typing.range_spec`
注意事项：仅做导出代理，不增加任何行为
"""

from lfx.field_typing.range_spec import RangeSpec

__all__ = ["RangeSpec"]
