"""
模块名称：LangWatch 组件导出入口

本模块用于集中暴露 LangWatch 评估组件，便于上层注册与发现流程统一引用。
主要功能：
- 对外导出 `LangWatchComponent` 类。

设计背景：避免上层依赖具体文件路径，保持组件导入路径稳定。
注意事项：新增导出对象时需同步更新 `__all__`。
"""

from .langwatch import LangWatchComponent

__all__ = ["LangWatchComponent"]
