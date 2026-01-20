"""
模块名称：`interface.utils` 兼容导出层

本模块提供工具函数的导出代理，主要用于保持旧路径兼容。主要功能包括：
- 转发 `lfx.interface.utils` 下的工具函数

关键组件：
- `lfx.interface.utils`：实际实现来源

设计背景：历史模块路径稳定性要求
注意事项：仅导出符号，不修改行为
"""

from lfx.interface.utils import *  # noqa: F403
