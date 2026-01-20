"""
模块名称：duckduckgo 组件入口

本模块导出 DuckDuckGo 搜索组件，提供稳定的导入路径。
主要功能包括：
- 功能1：暴露 `DuckDuckGoSearchComponent` 组件。

使用场景：在流程中调用 DuckDuckGo 进行网页搜索。
关键组件：
- 类 `DuckDuckGoSearchComponent`

设计背景：集中导出入口，避免外部依赖内部文件结构。
注意事项：新增导出项需同步更新 `__all__`。
"""

from .duck_duck_go_search_run import DuckDuckGoSearchComponent

__all__ = ["DuckDuckGoSearchComponent"]
