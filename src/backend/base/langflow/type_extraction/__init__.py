"""
模块名称：type_extraction 向后兼容模块

本模块提供向后兼容性支持，主要用于迁移过程中的API兼容。主要功能包括：
- 从新的 lfx.type_extraction 模块导入所有元素
- 确保旧版本API路径可用

设计背景：为了支持从旧版langflow到新版lfx的架构迁移，保持API兼容性
注意事项：新代码应直接使用 lfx.type_extraction 模块
"""

from lfx.type_extraction import *  # noqa: F403