"""
模块名称：lfx.components.git

本模块提供 Git 相关组件的统一导出入口。
主要功能包括：
- 导出仓库加载组件与仓库内容抽取组件

关键组件：
- `GitLoaderComponent`：仓库文件加载与过滤
- `GitExtractorComponent`：仓库信息/结构/内容抽取

设计背景：对 Git 仓库的加载与分析需要复用组件能力
使用场景：从本地或远程仓库加载内容用于下游处理
注意事项：该包直接导出组件，未使用懒加载
"""

from .git import GitLoaderComponent
from .gitextractor import GitExtractorComponent

__all__ = ["GitExtractorComponent", "GitLoaderComponent"]
