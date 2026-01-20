"""
模块名称：表格模型兼容导出

本模块转发 `lfx.schema.table` 的表格相关模型与类型，主要用于旧路径兼容。主要功能包括：
- 暴露列定义、校验与展示配置
- 暴露表格 schema 与选项

关键组件：
- TableSchema / TableOptions / Column
- VALID_TYPES

设计背景：历史代码仍依赖 `langflow.schema.table`。
注意事项：仅导出符号，行为由 `lfx` 实现决定。
"""

from lfx.schema.table import (
    VALID_TYPES,
    Column,
    EditMode,
    FieldParserType,
    FieldValidatorType,
    FormatterType,
    TableOptions,
    TableSchema,
)

__all__ = [
    "VALID_TYPES",
    "Column",
    "EditMode",
    "FieldParserType",
    "FieldValidatorType",
    "FormatterType",
    "TableOptions",
    "TableSchema",
]
