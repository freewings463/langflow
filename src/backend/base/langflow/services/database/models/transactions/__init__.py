"""
模块名称：交易日志模型导出

本模块导出交易日志表模型。
主要功能包括：暴露 `TransactionTable` 类型供上层使用。

关键组件：`TransactionTable`
设计背景：简化模型导入路径。
使用场景：日志写入与查询。
注意事项：脱敏与序列化逻辑在 `model.py` 中实现。
"""

from .model import TransactionTable

__all__ = ["TransactionTable"]
