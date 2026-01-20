"""
模块名称：`Data` 兼容导出

本模块转发 `lfx.schema.data` 中的 `Data` 与序列化函数，主要用于旧路径兼容。主要功能包括：
- 暴露 `Data` 数据结构
- 暴露 `custom_serializer` / `serialize_data`

关键组件：
- Data
- custom_serializer / serialize_data

设计背景：历史代码仍依赖 `langflow.schema.data`。
注意事项：仅导出符号，行为由 `lfx` 实现决定。
"""

from lfx.schema.data import Data, custom_serializer, serialize_data

__all__ = ["Data", "custom_serializer", "serialize_data"]
