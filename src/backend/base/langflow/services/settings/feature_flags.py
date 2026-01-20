"""
模块名称：Feature Flags 导出

本模块转发 Feature Flags 的定义。
主要功能包括：
- 暴露 `FEATURE_FLAGS` 常量

关键组件：
- `FEATURE_FLAGS`

设计背景：保持与 LFX feature flag 定义一致的导入路径。
注意事项：仅负责导出，不承载业务逻辑。
"""

from lfx.services.settings.feature_flags import FEATURE_FLAGS

__all__ = ["FEATURE_FLAGS"]
