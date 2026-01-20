"""
模块名称：缓存服务抽象基类

本模块定义同步/异步缓存服务的抽象接口，统一缓存操作契约。
主要功能：
- 定义 get/set/upsert/delete/clear 等抽象方法；
- 提供同步与异步缓存基类。

设计背景：统一不同缓存实现的接口，便于替换与扩展。
注意事项：具体实现需遵守返回 CACHE_MISS 的约定。
"""

import abc
import asyncio
import threading
from typing import Generic, TypeVar

from lfx.services.interfaces import CacheServiceProtocol

LockType = TypeVar("LockType", bound=threading.Lock)
AsyncLockType = TypeVar("AsyncLockType", bound=asyncio.Lock)


class CacheService(CacheServiceProtocol, Generic[LockType]):
    """同步缓存服务抽象基类"""

    name = "cache_service"

    @abc.abstractmethod
    def get(self, key, lock: LockType | None = None):
        """获取缓存值

        契约：命中返回值，未命中返回 CACHE_MISS。
        """

    @abc.abstractmethod
    def set(self, key, value, lock: LockType | None = None):
        """写入缓存值。"""

    @abc.abstractmethod
    def upsert(self, key, value, lock: LockType | None = None):
        """插入或更新缓存值。"""

    @abc.abstractmethod
    def delete(self, key, lock: LockType | None = None):
        """删除缓存值。"""

    @abc.abstractmethod
    def clear(self, lock: LockType | None = None):
        """清空缓存。"""

    @abc.abstractmethod
    def contains(self, key) -> bool:
        """判断 key 是否存在。"""

    @abc.abstractmethod
    def __contains__(self, key) -> bool:
        """`in` 语法支持。"""

    @abc.abstractmethod
    def __getitem__(self, key):
        """`cache[key]` 读取接口。"""

    @abc.abstractmethod
    def __setitem__(self, key, value) -> None:
        """`cache[key] = value` 写入接口。"""

    @abc.abstractmethod
    def __delitem__(self, key) -> None:
        """`del cache[key]` 删除接口。"""


class AsyncBaseCacheService(CacheServiceProtocol, Generic[AsyncLockType]):
    """异步缓存服务抽象基类。"""

    name = "cache_service"

    @abc.abstractmethod
    async def get(self, key, lock: AsyncLockType | None = None):
        """获取缓存值（异步）。"""

    @abc.abstractmethod
    async def set(self, key, value, lock: AsyncLockType | None = None):
        """写入缓存值（异步）。"""

    @abc.abstractmethod
    async def upsert(self, key, value, lock: AsyncLockType | None = None):
        """插入或更新缓存值（异步）。"""

    @abc.abstractmethod
    async def delete(self, key, lock: AsyncLockType | None = None):
        """删除缓存值（异步）。"""

    @abc.abstractmethod
    async def clear(self, lock: AsyncLockType | None = None):
        """清空缓存（异步）。"""

    @abc.abstractmethod
    async def contains(self, key) -> bool:
        """判断 key 是否存在（异步）。"""


class ExternalAsyncBaseCacheService(AsyncBaseCacheService):
    """外部异步缓存抽象基类。"""

    name = "cache_service"

    @abc.abstractmethod
    async def is_connected(self) -> bool:
        """判断外部缓存连接状态。"""
