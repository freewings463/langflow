"""
模块名称：Olivya 组件出口

本模块提供 Olivya 外呼组件的对外导出，供组件注册与自动发现使用。
主要功能包括：
- 暴露 `OlivyaComponent` 类

关键组件：
- `OlivyaComponent`

设计背景：简化组件索引与导入路径。
注意事项：该模块仅做导出，不包含业务逻辑。
"""

from .olivya import OlivyaComponent

__all__ = ["OlivyaComponent"]
