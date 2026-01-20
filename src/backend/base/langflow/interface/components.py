"""
模块名称：`interface.components` 兼容导出层

本模块提供组件相关接口的导出代理，主要用于保持旧路径兼容。主要功能包括：
- 转发 `lfx.interface.components` 下的组件接口

关键组件：
- `lfx.interface.components`：实际实现来源

设计背景：历史模块路径稳定性要求
注意事项：仅导出符号，不修改行为
"""

from lfx.interface.components import *  # noqa: F403
