"""
模块名称：自定义组件包导出

本模块聚合 `custom_component` 子包的常用导出，主要用于简化上层导入。主要功能包括：
- 暴露 `Component` 基类
- 暴露 `component` / `custom_component` 子模块

关键组件：
- Component
- component / custom_component

设计背景：历史代码存在 `langflow.custom.custom_component` 导入路径，需要兼容旧引用。
注意事项：仅转发符号；新增符号需同步更新 `__all__`。
"""

from lfx.custom.custom_component import component, custom_component
from lfx.custom.custom_component.component import Component

__all__ = ["Component", "component", "custom_component"]
