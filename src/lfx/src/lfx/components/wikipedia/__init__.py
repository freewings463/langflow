"""
模块名称：Wikipedia 相关组件导出入口

本模块提供 Wikipedia 与 Wikidata 组件的统一导出，主要用于简化组件注册与引用。主要功能包括：
- 汇总并暴露组件类
- 统一 `__all__` 以控制导出范围

关键组件：
- `WikidataComponent`：Wikidata 搜索组件
- `WikipediaComponent`：Wikipedia 查询组件

设计背景：将相关组件集中导出，减少上层导入路径复杂度。
使用场景：框架在加载组件目录时统一引用。
注意事项：当前为直接导入，若依赖变为可选可改为懒加载。
"""

from .wikidata import WikidataComponent
from .wikipedia import WikipediaComponent

__all__ = ["WikidataComponent", "WikipediaComponent"]
