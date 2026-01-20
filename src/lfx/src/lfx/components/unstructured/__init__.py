"""
模块名称：lfx.components.unstructured

本模块提供 Unstructured 组件的统一导出，主要用于组件注册/发现与外部引用。主要功能包括：
- 功能1：集中导出 `UnstructuredComponent` 供其他模块使用

关键组件：
- UnstructuredComponent：文件解析组件入口

设计背景：简化 import 路径并保持组件目录结构一致。
注意事项：仅做导出聚合，不包含业务逻辑。
"""

from .unstructured import UnstructuredComponent

# 对外导出的组件列表
__all__ = ["UnstructuredComponent"]
