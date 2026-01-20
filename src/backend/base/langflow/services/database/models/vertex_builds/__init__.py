"""
模块名称：节点构建模型导出

本模块导出节点构建相关模型。
主要功能包括：暴露 `VertexBuildTable` 类型。

关键组件：`VertexBuildTable`
设计背景：简化调用方导入路径。
使用场景：构建记录写入与查询。
注意事项：映射模型在 `model.py` 中定义。
"""

from .model import VertexBuildTable

__all__ = ["VertexBuildTable"]
