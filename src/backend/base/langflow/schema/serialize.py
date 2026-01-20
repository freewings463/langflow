"""
模块名称：序列化工具兼容导出

本模块转发 `lfx.schema.serialize` 的工具函数，主要用于旧路径兼容。主要功能包括：
- 暴露 `UUIDstr` 类型与 `str_to_uuid` 工具

关键组件：
- UUIDstr
- str_to_uuid

设计背景：历史代码仍依赖 `langflow.schema.serialize`。
注意事项：仅导出符号，行为由 `lfx` 实现决定。
"""
from lfx.schema.serialize import UUIDstr, str_to_uuid

__all__ = ["UUIDstr", "str_to_uuid"]
