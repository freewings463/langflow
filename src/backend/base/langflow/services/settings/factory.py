"""
模块名称：Settings Service 工厂导出

本模块转发 SettingsServiceFactory 的导出。
主要功能包括：
- 提供 settings 服务的工厂实例入口

关键组件：
- `SettingsServiceFactory`

设计背景：保持与 LFX settings 工厂一致的导入路径。
注意事项：仅负责导出，不承载业务逻辑。
"""

from lfx.services.settings.factory import SettingsServiceFactory

__all__ = ["SettingsServiceFactory"]
