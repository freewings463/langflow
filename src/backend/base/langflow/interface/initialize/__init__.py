"""
模块名称：`interface.initialize` 初始化入口

本模块提供初始化相关子模块的导出，主要用于对外暴露加载逻辑。主要功能包括：
- 导出 `loading` 子模块

关键组件：
- `loading`：组件实例化与构建逻辑

设计背景：统一初始化相关功能的导出路径
注意事项：仅导出模块，不触发初始化过程
"""

from . import loading

__all__ = ["loading"]
