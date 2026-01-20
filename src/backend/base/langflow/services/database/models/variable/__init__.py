"""
模块名称：变量模型导出

本模块导出变量相关模型。
主要功能包括：统一 `Variable` 创建/读取/更新模型导出。

关键组件：`Variable` / `VariableCreate` / `VariableRead` / `VariableUpdate`
设计背景：简化调用方导入路径。
使用场景：变量管理与凭据配置。
注意事项：凭据类型隐藏逻辑在 `model.py` 中实现。
"""

from .model import Variable, VariableCreate, VariableRead, VariableUpdate

__all__ = ["Variable", "VariableCreate", "VariableRead", "VariableUpdate"]
