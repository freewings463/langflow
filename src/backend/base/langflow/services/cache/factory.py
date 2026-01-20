"""
模块名称：缓存服务工厂

本模块提供缓存服务的创建工厂，主要用于根据配置选择缓存实现。主要功能包括：
- 根据 `cache_type` 实例化内存/磁盘/`Redis` 缓存

关键组件：
- `CacheServiceFactory`：缓存服务工厂

设计背景：缓存实现依赖配置项，需要统一构造入口
注意事项：未知 `cache_type` 返回 `None`
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lfx.log.logger import logger
from typing_extensions import override

from langflow.services.cache.disk import AsyncDiskCache
from langflow.services.cache.service import AsyncInMemoryCache, CacheService, RedisCache, ThreadingInMemoryCache
from langflow.services.factory import ServiceFactory

if TYPE_CHECKING:
    from lfx.services.settings.service import SettingsService


class CacheServiceFactory(ServiceFactory):
    """缓存服务工厂。"""

    def __init__(self) -> None:
        super().__init__(CacheService)

    @override
    def create(self, settings_service: SettingsService):
        """根据配置创建缓存服务实例。

        契约：输入 `settings_service`；输出缓存实例或 `None`。
        关键路径：依据 `settings_service.settings.cache_type` 分支创建实现。
        失败语义：配置缺失时由调用方处理；未知类型返回 `None`。
        """

        if settings_service.settings.cache_type == "redis":
            logger.debug("Creating Redis cache")
            return RedisCache(
                host=settings_service.settings.redis_host,
                port=settings_service.settings.redis_port,
                db=settings_service.settings.redis_db,
                url=settings_service.settings.redis_url,
                expiration_time=settings_service.settings.redis_cache_expire,
            )

        if settings_service.settings.cache_type == "memory":
            return ThreadingInMemoryCache(expiration_time=settings_service.settings.cache_expire)
        if settings_service.settings.cache_type == "async":
            return AsyncInMemoryCache(expiration_time=settings_service.settings.cache_expire)
        if settings_service.settings.cache_type == "disk":
            return AsyncDiskCache(
                cache_dir=settings_service.settings.config_dir,
                expiration_time=settings_service.settings.cache_expire,
            )
        return None
