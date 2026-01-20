"""
模块名称：`component` 兼容导出

本模块从 `lfx.custom.custom_component.component` 转发核心组件与配置常量，供旧路径使用。主要功能包括：
- 暴露 `Component` / `PlaceholderGraph` 与配置常量
- 提供 `get_component_toolkit` 及其私有别名以兼容旧调用

关键组件：
- `Component`: 自定义组件基类
- `get_component_toolkit`: 组件工具集入口

设计背景：历史代码依赖 `langflow.custom.custom_component.component` 导入路径。
注意事项：仅做符号转发；`_get_component_toolkit` 为旧私有名，未来可能移除。
"""

from lfx.custom.custom_component.component import (
    BACKWARDS_COMPATIBLE_ATTRIBUTES,
    CONFIG_ATTRIBUTES,
    Component,
    PlaceholderGraph,
    get_component_toolkit,
)

# 决策：保留 `_get_component_toolkit` 私有别名
# 问题：旧代码仍直接引用私有函数名
# 方案：在兼容层映射到新版 `get_component_toolkit`
# 代价：继续暴露私有接口，难以清理依赖
# 重评：当下游无旧调用后移除该别名
_get_component_toolkit = get_component_toolkit

# 注意：显式导出用于稳定对外 `API`，新增符号需同步更新。
__all__ = [
    "BACKWARDS_COMPATIBLE_ATTRIBUTES",
    "CONFIG_ATTRIBUTES",
    "Component",
    "PlaceholderGraph",
    "_get_component_toolkit",
    "get_component_toolkit",
]
