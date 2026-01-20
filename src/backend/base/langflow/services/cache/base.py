"""
模块名称：缓存服务抽象基类

本模块定义同步/异步缓存服务的抽象接口，主要用于统一缓存实现的契约与生命周期。主要功能包括：
- 提供同步缓存的 `get`/`set`/`upsert`/`delete`/`clear` 等接口
- 提供异步缓存的对应接口与连接状态检查

关键组件：
- `CacheService`：同步缓存接口
- `AsyncBaseCacheService`：异步缓存接口
- `ExternalAsyncBaseCacheService`：外部依赖缓存接口

设计背景：缓存实现多样（内存/磁盘/`Redis`），需统一契约
注意事项：接口返回 `CACHE_MISS` 语义由具体实现定义
"""

import abc
import asyncio
import threading
from typing import Generic, TypeVar

from langflow.services.base import Service

LockType = TypeVar("LockType", bound=threading.Lock)
AsyncLockType = TypeVar("AsyncLockType", bound=asyncio.Lock)


class CacheService(Service, Generic[LockType]):
    """同步缓存服务抽象基类。

    契约：提供键值缓存的读写/删除/清空/存在性判断接口。
    注意：具体实现需定义 `CACHE_MISS` 的语义与线程安全策略。
    """

    name = "cache_service"

    @abc.abstractmethod
    def get(self, key, lock: LockType | None = None):
        """读取缓存项。

        契约：输入 `key` 与可选锁；输出命中值或 `CACHE_MISS`。
        失败语义：实现可选择抛异常或返回 `CACHE_MISS`。
        """

    @abc.abstractmethod
    def set(self, key, value, lock: LockType | None = None):
        """写入缓存项。

        契约：输入 `key`/`value` 与可选锁；无返回值；允许覆盖。
        """

    @abc.abstractmethod
    def upsert(self, key, value, lock: LockType | None = None):
        """插入或更新缓存项。

        契约：输入 `key`/`value` 与可选锁；无返回值；实现可支持合并语义。
        """

    @abc.abstractmethod
    def delete(self, key, lock: LockType | None = None):
        """删除缓存项。

        契约：输入 `key` 与可选锁；无返回值；不存在时应静默处理。
        """

    @abc.abstractmethod
    def clear(self, lock: LockType | None = None):
        """清空缓存。

        契约：无返回值；实现需保证清空语义。
        """

    @abc.abstractmethod
    def contains(self, key) -> bool:
        """判断键是否存在于缓存。

        契约：输入 `key`；输出 `bool`。
        """

    @abc.abstractmethod
    def __contains__(self, key) -> bool:
        """`in` 操作的存在性判断。"""

    @abc.abstractmethod
    def __getitem__(self, key):
        """下标读取语义，等价于 `get`。"""

    @abc.abstractmethod
    def __setitem__(self, key, value) -> None:
        """下标写入语义，等价于 `set`。"""

    @abc.abstractmethod
    def __delitem__(self, key) -> None:
        """下标删除语义，等价于 `delete`。"""


class AsyncBaseCacheService(Service, Generic[AsyncLockType]):
    """异步缓存服务抽象基类。

    契约：提供异步键值缓存的读写/删除/清空/存在性判断接口。
    """

    name = "cache_service"

    @abc.abstractmethod
    async def get(self, key, lock: AsyncLockType | None = None):
        """读取缓存项（异步）。

        契约：输入 `key` 与可选锁；输出命中值或 `CACHE_MISS`。
        """

    @abc.abstractmethod
    async def set(self, key, value, lock: AsyncLockType | None = None):
        """写入缓存项（异步）。"""

    @abc.abstractmethod
    async def upsert(self, key, value, lock: AsyncLockType | None = None):
        """插入或更新缓存项（异步）。"""

    @abc.abstractmethod
    async def delete(self, key, lock: AsyncLockType | None = None):
        """删除缓存项（异步）。"""

    @abc.abstractmethod
    async def clear(self, lock: AsyncLockType | None = None):
        """清空缓存（异步）。"""

    @abc.abstractmethod
    async def contains(self, key) -> bool:
        """判断键是否存在于缓存（异步）。"""


class ExternalAsyncBaseCacheService(AsyncBaseCacheService):
    """外部依赖异步缓存抽象基类。

    契约：在异步缓存基础上增加连接状态检查接口。
    """

    name = "cache_service"

    @abc.abstractmethod
    async def is_connected(self) -> bool:
        """检查外部缓存是否已连接。"""
