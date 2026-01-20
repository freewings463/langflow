"""
模块名称：共享组件缓存服务工厂

本模块提供共享组件缓存服务的创建工厂。
主要功能包括：
- 创建并配置 `SharedComponentCacheService` 实例
- 注入缓存过期时间参数

设计背景：通过工厂统一服务初始化逻辑。
注意事项：`expiration_time` 默认 1 小时。
"""

from typing import TYPE_CHECKING

from lfx.services.factory import ServiceFactory
from lfx.services.shared_component_cache.service import SharedComponentCacheService

if TYPE_CHECKING:
    from lfx.services.base import Service


class SharedComponentCacheServiceFactory(ServiceFactory):
    """Factory for creating SharedComponentCacheService instances."""

    def __init__(self) -> None:
        """初始化工厂并绑定服务类。"""
        super().__init__()
        self.service_class = SharedComponentCacheService

    def create(self, **kwargs) -> "Service":
        """创建共享组件缓存服务实例。

        契约：支持传入 `expiration_time`，默认 3600 秒。
        """
        expiration_time = kwargs.get("expiration_time", 60 * 60)  # 注意：默认 1 小时
        return SharedComponentCacheService(expiration_time=expiration_time)
