"""
模块名称：输入字段混入兼容层

本模块提供 `lfx` 输入混入类的转发导入，主要用于保持旧继承路径可用。
主要功能包括：
- 暴露 `AuthMixin`、`FileMixin`、`PromptFieldMixin` 等混入类型。

关键组件：各类 `*Mixin` 与 `FieldTypes`。
设计背景：输入字段能力已迁移至 `lfx`，需要旧路径兼容。
使用场景：历史输入类或插件继续继承 `langflow.inputs.input_mixin`。
注意事项：此处仅重导出，避免引入额外依赖。
"""

from lfx.inputs.input_mixin import (
    AuthMixin,
    BaseInputMixin,
    ConnectionMixin,
    DatabaseLoadMixin,
    DropDownMixin,
    FieldTypes,
    FileMixin,
    InputTraceMixin,
    LinkMixin,
    ListableInputMixin,
    McpMixin,
    MetadataTraceMixin,
    MultilineMixin,
    PromptFieldMixin,
    QueryMixin,
    RangeMixin,
    SerializableFieldTypes,
    SliderMixin,
    SortableListMixin,
    TableMixin,
    TabMixin,
    ToolModeMixin,
    ToolsMixin,
)

__all__ = [
    "AuthMixin",
    "BaseInputMixin",
    "ConnectionMixin",
    "DatabaseLoadMixin",
    "DropDownMixin",
    "FieldTypes",
    "FileMixin",
    "InputTraceMixin",
    "LinkMixin",
    "ListableInputMixin",
    "McpMixin",
    "MetadataTraceMixin",
    "MultilineMixin",
    "PromptFieldMixin",
    "QueryMixin",
    "RangeMixin",
    "SerializableFieldTypes",
    "SliderMixin",
    "SortableListMixin",
    "TabMixin",
    "TableMixin",
    "ToolModeMixin",
    "ToolsMixin",
]
