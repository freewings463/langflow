"""
模块名称：`io` Schema 入口

本模块统一导出 `lfx.io.schema` 的输入 schema 构建与转换工具，供组件定义与表单生成使用。主要功能包括：
- 构建 schema：`create_input_schema`/`create_input_schema_from_dict`
- 转换与扁平化：`flatten_schema`/`schema_to_langflow_inputs`

关键组件：
- `__all__`：限制对外导出符号范围

设计背景：集中导出，降低上层对具体实现位置的耦合。
注意事项：仅做导出聚合，不包含额外校验逻辑。
"""

from lfx.io.schema import (
    create_input_schema,
    create_input_schema_from_dict,
    flatten_schema,
    schema_to_langflow_inputs,
)

__all__ = [
    "create_input_schema",
    "create_input_schema_from_dict",
    "flatten_schema",
    "schema_to_langflow_inputs",
]
