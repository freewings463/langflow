"""
模块名称：arXiv 组件导出

本模块集中导出 arXiv 组件，供组件注册与引用使用。
注意事项：新增导出需同步更新 `__all__`。
"""

from .arxiv import ArXivComponent

__all__ = ["ArXivComponent"]
