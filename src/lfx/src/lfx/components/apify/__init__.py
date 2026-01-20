"""
模块名称：apify 组件入口

本模块对外导出 Apify 相关组件，保持稳定导入路径。
主要功能包括：
- 功能1：暴露 `ApifyActorsComponent` 作为 Apify Actor 组件入口。

使用场景：流程或工具需要通过 Apify Actor 获取外部数据时。
关键组件：
- 类 `ApifyActorsComponent`

设计背景：集中导出入口，避免外部依赖内部文件结构变化。
注意事项：新增导出项需同步更新 `__all__`。
"""

from .apify_actor import ApifyActorsComponent

__all__ = [
    "ApifyActorsComponent",
]
