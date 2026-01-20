"""
模块名称：mustache_security

本模块提供Mustache模板安全性相关的实用工具，主要用于向后兼容。
主要功能包括：
- 从新的lfx.utils.mustache_security模块导入所有元素
- 确保旧版本API路径可用

设计背景：为了支持从旧版langflow到新版lfx的架构迁移，保持API兼容性
注意事项：新代码应直接使用 lfx.utils.mustache_security 模块
"""

from lfx.utils.mustache_security import *  # noqa: F403
