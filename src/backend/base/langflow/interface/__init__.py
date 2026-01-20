"""
模块名称：`interface` 兼容导出层

本模块提供 `lfx.interface` 的统一导出入口，主要用于保持历史导入路径稳定。主要功能包括：
- 转发 `lfx.interface` 下的接口与工具

关键组件：
- `lfx.interface`：实际实现来源

设计背景：旧代码依赖 `langflow.interface`，迁移后需保持兼容
注意事项：仅做导出代理，不增加运行时逻辑
"""

from lfx.interface import *  # noqa: F403
