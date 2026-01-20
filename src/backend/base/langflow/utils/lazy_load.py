"""
模块名称：lazy_load

本模块提供延迟加载实用工具，主要用于向后兼容。
主要功能包括：
- 从新的lfx.utils.lazy_load模块导入所有元素
- 确保旧版本API路径可用

设计背景：为了支持从旧版langflow到新版lfx的架构迁移，保持API兼容性
注意事项：新代码应直接使用 lfx.utils.lazy_load 模块
"""

# Import everything from lfx.utils.lazy_load
from lfx.utils.lazy_load import *  # noqa: F403
