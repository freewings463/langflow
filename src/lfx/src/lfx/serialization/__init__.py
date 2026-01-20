"""模块名称：序列化工具包入口

本模块作为序列化工具的导出入口，统一暴露 `serialize/serialize_or_str`。
主要功能包括：保持包级 API 稳定，集中管理导出符号。

关键组件：
- `serialize`：统一序列化入口
- `serialize_or_str`：序列化失败时回退字符串

设计背景：为上层提供稳定的序列化调用路径。
注意事项：实际实现位于 `serialization.py`。
"""

from .serialization import serialize, serialize_or_str

__all__ = ["serialize", "serialize_or_str"]
