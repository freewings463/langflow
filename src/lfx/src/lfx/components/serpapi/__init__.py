"""模块名称：SerpAPI 组件导出层

本模块负责导出 SerpAPI 相关组件，供上层按需引用。
主要功能包括：暴露 `SerpComponent` 并集中维护导出列表。

关键组件：
- `SerpComponent`：SerpAPI 搜索组件入口

设计背景：统一组件导出入口，减少外部导入路径耦合。
注意事项：新增组件需同步更新 `__all__`。
"""

from .serp import SerpComponent

__all__ = ["SerpComponent"]
