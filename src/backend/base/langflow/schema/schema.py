"""
模块名称：`schema` 兼容导出

本模块转发 `lfx.schema.schema` 的类型与工具函数，主要用于旧路径兼容。主要功能包括：
- 暴露输入/输出类型与常量
- 暴露输出日志构建工具

关键组件：
- InputType / OutputType / OutputValue
- build_output_logs / get_type

设计背景：历史代码仍依赖 `langflow.schema.schema`。
注意事项：仅导出符号，行为由 `lfx` 实现决定。
"""
from lfx.schema.schema import (
    INPUT_FIELD_NAME,
    ErrorLog,
    InputType,
    LogType,
    OutputType,
    OutputValue,
    StreamURL,
    build_output_logs,
    get_type,
)

__all__ = [
    "INPUT_FIELD_NAME",
    "ErrorLog",
    "InputType",
    "LogType",
    "OutputType",
    "OutputValue",
    "StreamURL",
    "build_output_logs",
    "get_type",
]
