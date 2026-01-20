"""
模块名称：服务初始化入口

本模块负责在导入时注册基础服务工厂。
主要功能包括：
- 注册 Settings 服务工厂
- 提供延迟创建策略

设计背景：保证服务管理器具备最小可用配置。
注意事项：这里只注册工厂，不立即实例化服务。
"""

from lfx.services.settings.factory import SettingsServiceFactory


def initialize_services():
    """初始化必需服务工厂。"""
    from lfx.services.manager import get_service_manager

    # 注意：注册设置服务工厂，保证后续可获取配置。
    service_manager = get_service_manager()
    service_manager.register_factory(SettingsServiceFactory())

    # 注意：不立即创建服务实例，首次访问时再创建。


# 注意：模块导入时执行初始化。
initialize_services()
