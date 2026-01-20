"""
模块名称：Session 服务工厂

本模块提供 `SessionService` 的工厂实现，统一服务构造方式。
主要功能：基于 `ServiceFactory` 创建 `SessionService` 实例。
设计背景：服务注册与依赖注入的标准化入口。
注意事项：创建依赖 `CacheService`，未提供则无法使用。
"""

from typing import TYPE_CHECKING

from typing_extensions import override

from langflow.services.factory import ServiceFactory
from langflow.services.session.service import SessionService

if TYPE_CHECKING:
    from langflow.services.cache.service import CacheService


class SessionServiceFactory(ServiceFactory):
    """SessionService 工厂类。"""

    def __init__(self) -> None:
        super().__init__(SessionService)

    @override
    def create(self, cache_service: "CacheService"):
        """创建 SessionService 实例并注入缓存服务。"""
        return SessionService(cache_service)
