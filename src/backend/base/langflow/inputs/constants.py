"""
模块名称：输入常量兼容层

本模块提供对 `lfx.inputs.constants` 的转发导入，主要用于保持旧导入路径可用。
主要功能包括：
- 暴露 `MAX_TAB_OPTIONS` 与 `MAX_TAB_OPTION_LENGTH`。

关键组件：常量本身。
设计背景：输入组件常量已迁移至 `lfx`，需要兼容历史引用路径。
使用场景：旧代码从 `langflow.inputs.constants` 读取输入限制配置。
注意事项：常量值以 `lfx` 为准，本模块不应覆写。
"""

from lfx.inputs.constants import MAX_TAB_OPTION_LENGTH, MAX_TAB_OPTIONS

__all__ = ["MAX_TAB_OPTIONS", "MAX_TAB_OPTION_LENGTH"]
