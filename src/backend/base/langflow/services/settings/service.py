"""
模块名称：Settings Service 导出

本模块转发 SettingsService 的导出。
主要功能包括：
- 暴露 settings 服务实例类型

关键组件：
- `SettingsService`

设计背景：保持与 LFX settings service 一致的导入路径。
注意事项：仅负责导出，不承载业务逻辑。
"""

from lfx.services.settings.service import SettingsService

__all__ = ["SettingsService"]
