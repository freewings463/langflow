"""
模块名称：服务管理器（Langflow 兼容层）

本模块提供 Langflow 服务管理器的兼容层，重新导出来自 lfx 的功能。
主要功能包括：
- 重新导出 lfx 服务管理器功能
- 为旧版 Langflow 代码提供向后兼容性
- 服务初始化功能

关键组件：
- `ServiceManager`：服务管理器类
- `get_service_manager`：获取服务管理器实例
- `initialize_*_service`：服务初始化函数

设计背景：提供从旧版 Langflow 代码到新版 lfx 服务架构的过渡。
注意事项：这是为了向后兼容而保留的模块。
"""

from __future__ import annotations

# Re-export everything from lfx
from lfx.services.manager import NoFactoryRegisteredError, ServiceManager, get_service_manager

__all__ = ["NoFactoryRegisteredError", "ServiceManager", "get_service_manager"]


def initialize_settings_service() -> None:
    """初始化设置管理器。

    契约：注册设置服务工厂。
    副作用：修改服务管理器状态。
    失败语义：如果注册失败则抛出异常。
    
    决策：将初始化逻辑封装在函数中
    问题：设置服务需要在其他服务之前初始化
    方案：提供专门的初始化函数
    代价：增加了初始化的复杂性
    重评：如果初始化逻辑变得更复杂则考虑重构
    """
    from lfx.services.settings import factory as settings_factory

    get_service_manager().register_factory(settings_factory.SettingsServiceFactory())


def initialize_session_service() -> None:
    """初始化会话管理器。

    契约：注册缓存和会话服务工厂。
    副作用：修改服务管理器状态。
    失败语义：如果注册失败则抛出异常。
    """
    from langflow.services.cache import factory as cache_factory
    from langflow.services.session import factory as session_service_factory

    initialize_settings_service()

    get_service_manager().register_factory(cache_factory.CacheServiceFactory())
    get_service_manager().register_factory(session_service_factory.SessionServiceFactory())