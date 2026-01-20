"""
模块名称：Confluence 组件包入口

本模块用于导出 Confluence 组件，提供稳定的包级引用路径。主要功能包括：
- 作为 `lfx.components.confluence` 的包级入口
- 暴露 `ConfluenceComponent`

关键组件：
- `ConfluenceComponent`

设计背景：组件集中管理，避免上层直接依赖文件路径。
使用场景：上层通过包入口引用 Confluence 组件。
注意事项：仅导出组件符号，不包含运行逻辑。
"""

from .confluence import ConfluenceComponent

__all__ = ["ConfluenceComponent"]
