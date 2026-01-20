"""
模块名称：Yahoo Finance 组件导出入口

本模块提供 Yahoo Finance 相关组件的导出入口，便于上层统一导入。
主要功能：
- 对外导出 `YfinanceComponent`。

设计背景：统一组件导入路径，避免上层依赖具体文件位置。
注意事项：新增组件需同步更新 `__all__`。
"""

from .yahoo import YfinanceComponent

__all__ = ["YfinanceComponent"]
