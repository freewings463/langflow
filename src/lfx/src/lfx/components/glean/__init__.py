"""
模块名称：`Glean` 组件包

本模块提供 `Glean` 组件的包级入口。
使用场景：对外导出 `GleanSearchAPISchema` 等公共定义。
注意事项：仅承载包文档与导出控制。
"""

from .glean_search_api import GleanSearchAPISchema

__all__ = ["GleanSearchAPISchema"]
