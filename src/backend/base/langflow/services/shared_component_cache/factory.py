"""
模块名称：共享组件缓存服务工厂

本模块提供共享组件缓存服务的创建工厂，主要用于依赖注入与配置绑定。主要功能包括：
- 将 `SettingsService` 的 `cache_expire` 配置注入到服务实例

关键组件：
- SharedComponentCacheServiceFactory

设计背景：服务实例化需要与配置解耦，统一由工厂管理。
注意事项：`cache_expire` 为全局配置，修改会影响所有共享缓存条目。
"""

from typing import TYPE_CHECKING

from typing_extensions import override

from langflow.services.factory import ServiceFactory
from langflow.services.shared_component_cache.service import SharedComponentCacheService

if TYPE_CHECKING:
    from lfx.services.settings.service import SettingsService


class SharedComponentCacheServiceFactory(ServiceFactory):
    """共享组件缓存服务工厂。

    契约：`create` 需要 `SettingsService`；返回 `SharedComponentCacheService` 实例。
    副作用：无。
    失败语义：配置缺失或不合法时由 `SettingsService` 抛出异常。
    """

    def __init__(self) -> None:
        super().__init__(SharedComponentCacheService)

    @override
    def create(self, settings_service: "SettingsService"):
        """按配置创建共享缓存服务。

        关键路径（三步）：
        1) 读取 `settings_service.settings.cache_expire`。
        2) 构造 `SharedComponentCacheService`。
        3) 返回服务实例。
        """
        return SharedComponentCacheService(expiration_time=settings_service.settings.cache_expire)
