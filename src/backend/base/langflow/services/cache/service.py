"""
模块名称：缓存服务实现

本模块提供内存与 `Redis` 缓存的具体实现，主要用于统一缓存行为并提供同步/异步访问。主要功能包括：
- 基于 `OrderedDict` 的 `LRU` 内存缓存
- 基于 `redis.asyncio` 的外部缓存
- 异步内存缓存实现

关键组件：
- `ThreadingInMemoryCache`：线程安全内存缓存
- `RedisCache`：`Redis` 缓存
- `AsyncInMemoryCache`：异步内存缓存

设计背景：不同运行模式需要可替换的缓存实现
注意事项：命中失败使用 `CACHE_MISS` 哨兵；序列化使用 `pickle`/`dill`
"""

import asyncio
import pickle
import threading
import time
from collections import OrderedDict
from typing import Generic, Union

import dill
from lfx.log.logger import logger
from lfx.services.cache.utils import CACHE_MISS
from typing_extensions import override

from langflow.services.cache.base import (
    AsyncBaseCacheService,
    AsyncLockType,
    CacheService,
    ExternalAsyncBaseCacheService,
    LockType,
)


class ThreadingInMemoryCache(CacheService, Generic[LockType]):
    """线程安全的内存缓存实现。

    契约：提供同步缓存接口；命中失败返回 `CACHE_MISS`。
    关键路径：使用 `OrderedDict` 实现 `LRU`；以时间戳控制过期。
    失败语义：缓存值为不可反序列化的 `bytes` 时抛 `pickle.UnpicklingError`。
    注意：`expiration_time=None` 表示不过期。
    """

    def __init__(self, max_size=None, expiration_time=60 * 60) -> None:
        """初始化内存缓存。

        契约：输入 `max_size`/`expiration_time`；原地初始化缓存与锁。
        """
        self._cache: OrderedDict = OrderedDict()
        self._lock = threading.RLock()
        self.max_size = max_size
        self.expiration_time = expiration_time

    def get(self, key, lock: Union[threading.Lock, None] = None):  # noqa: UP007
        """读取缓存项。

        契约：输入 `key` 与可选锁；输出命中值或 `CACHE_MISS`。
        失败语义：缓存值为不可反序列化 `bytes` 时抛异常。
        """
        with lock or self._lock:
            return self._get_without_lock(key)

    def _get_without_lock(self, key):
        if item := self._cache.get(key):
            if self.expiration_time is None or time.time() - item["time"] < self.expiration_time:
                self._cache.move_to_end(key)
                return pickle.loads(item["value"]) if isinstance(item["value"], bytes) else item["value"]
            self.delete(key)
        return CACHE_MISS

    def set(self, key, value, lock: Union[threading.Lock, None] = None) -> None:  # noqa: UP007
        """写入缓存项。

        契约：输入 `key`/`value` 与可选锁；无返回值；容量满时淘汰最久未使用项。
        """
        with lock or self._lock:
            if key in self._cache:
                self.delete(key)
            elif self.max_size and len(self._cache) >= self.max_size:
                self._cache.popitem(last=False)

            self._cache[key] = {"value": value, "time": time.time()}

    def upsert(self, key, value, lock: Union[threading.Lock, None] = None) -> None:  # noqa: UP007
        """插入或更新缓存项。

        契约：若旧值与新值均为 `dict`，则合并后写回。
        """
        with lock or self._lock:
            existing_value = self._get_without_lock(key)
            if existing_value is not CACHE_MISS and isinstance(existing_value, dict) and isinstance(value, dict):
                existing_value.update(value)
                value = existing_value

            self.set(key, value)

    def get_or_set(self, key, value, lock: Union[threading.Lock, None] = None):  # noqa: UP007
        """读取缓存项，未命中则写入并返回给定值。"""
        with lock or self._lock:
            if key in self._cache:
                return self.get(key)
            self.set(key, value)
            return value

    def delete(self, key, lock: Union[threading.Lock, None] = None) -> None:  # noqa: UP007
        """删除缓存项。"""
        with lock or self._lock:
            self._cache.pop(key, None)

    def clear(self, lock: Union[threading.Lock, None] = None) -> None:  # noqa: UP007
        """清空缓存。"""
        with lock or self._lock:
            self._cache.clear()

    def contains(self, key) -> bool:
        """判断键是否存在于缓存。"""
        return key in self._cache

    def __contains__(self, key) -> bool:
        """`in` 操作的存在性判断。"""
        return self.contains(key)

    def __getitem__(self, key):
        """下标读取语义，等价于 `get`。"""
        return self.get(key)

    def __setitem__(self, key, value) -> None:
        """下标写入语义，等价于 `set`。"""
        self.set(key, value)

    def __delitem__(self, key) -> None:
        """下标删除语义，等价于 `delete`。"""
        self.delete(key)

    def __len__(self) -> int:
        """返回缓存项数量。"""
        return len(self._cache)

    def __repr__(self) -> str:
        """返回实例信息字符串。"""
        return f"InMemoryCache(max_size={self.max_size}, expiration_time={self.expiration_time})"


class RedisCache(ExternalAsyncBaseCacheService, Generic[LockType]):
    """基于 `Redis` 的异步缓存实现。

    契约：使用 `dill` 序列化值；未命中返回 `CACHE_MISS`。
    关键路径：写入使用 `setex`，过期时间由 `expiration_time` 控制。
    失败语义：连接失败在 `is_connected` 返回 `False`；序列化失败抛 `TypeError`。
    注意：该实现标记为实验特性，初始化时会记录告警日志。
    """

    def __init__(self, host="localhost", port=6379, db=0, url=None, expiration_time=60 * 60) -> None:
        """初始化 `Redis` 缓存客户端。"""
        from redis.asyncio import StrictRedis

        logger.warning(
            "RedisCache is an experimental feature and may not work as expected."
            " Please report any issues to our GitHub repository."
        )
        if url:
            self._client = StrictRedis.from_url(url)
        else:
            self._client = StrictRedis(host=host, port=port, db=db)
        self.expiration_time = expiration_time

    async def is_connected(self) -> bool:
        """检查 `Redis` 客户端连通性。"""
        import redis

        try:
            await self._client.ping()
        except redis.exceptions.ConnectionError:
            msg = "RedisCache could not connect to the Redis server"
            await logger.aexception(msg)
            return False
        return True

    @override
    async def get(self, key, lock=None):
        """读取缓存值。"""
        if key is None:
            return CACHE_MISS
        value = await self._client.get(str(key))
        return dill.loads(value) if value else CACHE_MISS

    @override
    async def set(self, key, value, lock=None) -> None:
        """写入缓存值。"""
        try:
            if pickled := dill.dumps(value, recurse=True):
                result = await self._client.setex(str(key), self.expiration_time, pickled)
                if not result:
                    msg = "RedisCache could not set the value."
                    raise ValueError(msg)
        except pickle.PicklingError as exc:
            msg = "RedisCache only accepts values that can be pickled. "
            raise TypeError(msg) from exc

    @override
    async def upsert(self, key, value, lock=None) -> None:
        """插入或更新缓存项。

        契约：若旧值与新值均为 `dict`，则合并后写回。
        """
        if key is None:
            return
        existing_value = await self.get(key)
        if existing_value is not None and isinstance(existing_value, dict) and isinstance(value, dict):
            existing_value.update(value)
            value = existing_value

        await self.set(key, value)

    @override
    async def delete(self, key, lock=None) -> None:
        """删除缓存项。"""
        await self._client.delete(key)

    @override
    async def clear(self, lock=None) -> None:
        """清空缓存数据库。"""
        await self._client.flushdb()

    async def contains(self, key) -> bool:
        """判断键是否存在于缓存。"""
        if key is None:
            return False
        return bool(await self._client.exists(str(key)))

    def __repr__(self) -> str:
        """返回实例信息字符串。"""
        return f"RedisCache(expiration_time={self.expiration_time})"


class AsyncInMemoryCache(AsyncBaseCacheService, Generic[AsyncLockType]):
    """异步内存缓存实现。

    契约：提供异步缓存接口；命中失败返回 `CACHE_MISS`。
    关键路径：使用 `OrderedDict` 维护访问顺序并记录时间戳。
    失败语义：缓存值为不可反序列化的 `bytes` 时抛异常。
    """

    def __init__(self, max_size=None, expiration_time=3600) -> None:
        self.cache: OrderedDict = OrderedDict()

        self.lock = asyncio.Lock()
        self.max_size = max_size
        self.expiration_time = expiration_time

    async def get(self, key, lock: asyncio.Lock | None = None):
        """读取缓存项。"""
        async with lock or self.lock:
            return await self._get(key)

    async def _get(self, key):
        item = self.cache.get(key, None)
        if item:
            if time.time() - item["time"] < self.expiration_time:
                self.cache.move_to_end(key)
                return pickle.loads(item["value"]) if isinstance(item["value"], bytes) else item["value"]
            await logger.ainfo(f"Cache item for key '{key}' has expired and will be deleted.")
            await self._delete(key)
        return CACHE_MISS

    async def set(self, key, value, lock: asyncio.Lock | None = None) -> None:
        """写入缓存项。"""
        async with lock or self.lock:
            await self._set(
                key,
                value,
            )

    async def _set(self, key, value) -> None:
        if self.max_size and len(self.cache) >= self.max_size:
            self.cache.popitem(last=False)
        self.cache[key] = {"value": value, "time": time.time()}
        self.cache.move_to_end(key)

    async def delete(self, key, lock: asyncio.Lock | None = None) -> None:
        """删除缓存项。"""
        async with lock or self.lock:
            await self._delete(key)

    async def _delete(self, key) -> None:
        if key in self.cache:
            del self.cache[key]

    async def clear(self, lock: asyncio.Lock | None = None) -> None:
        """清空缓存。"""
        async with lock or self.lock:
            await self._clear()

    async def _clear(self) -> None:
        self.cache.clear()

    async def upsert(self, key, value, lock: asyncio.Lock | None = None) -> None:
        """插入或更新缓存项。"""
        await self._upsert(key, value, lock)

    async def _upsert(self, key, value, lock: asyncio.Lock | None = None) -> None:
        existing_value = await self.get(key, lock)
        if existing_value is not None and isinstance(existing_value, dict) and isinstance(value, dict):
            existing_value.update(value)
            value = existing_value
        await self.set(key, value, lock)

    async def contains(self, key) -> bool:
        """判断键是否存在于缓存。"""
        return key in self.cache
