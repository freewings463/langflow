"""
模块名称：磁盘异步缓存实现

本模块提供基于 `diskcache` 的异步缓存实现，主要用于在磁盘上持久化缓存并保持异步接口。主要功能包括：
- 以 `asyncio.to_thread` 方式包装磁盘 I/O
- 通过时间戳实现过期控制与访问刷新

关键组件：
- `AsyncDiskCache`：磁盘缓存实现

设计背景：提供与内存缓存一致的接口，同时允许落盘
注意事项：磁盘操作在后台线程中执行；异常由 `diskcache`/`pickle` 抛出
"""

import asyncio
import pickle
import time
from typing import Generic

from diskcache import Cache
from lfx.log.logger import logger
from lfx.services.cache.utils import CACHE_MISS

from langflow.services.cache.base import AsyncBaseCacheService, AsyncLockType


class AsyncDiskCache(AsyncBaseCacheService, Generic[AsyncLockType]):
    """基于 `diskcache` 的异步缓存。

    契约：提供 `get`/`set`/`upsert`/`delete`/`clear`/`contains` 异步接口；未命中返回 `CACHE_MISS`。
    关键路径：磁盘读写通过 `asyncio.to_thread` 执行；用时间戳控制过期。
    失败语义：磁盘读写/反序列化失败时抛出对应异常。
    决策：初始化时清空磁盘缓存
    问题：磁盘缓存跨进程持久化会与内存缓存行为不一致
    方案：实例化时若缓存非空则清空
    代价：失去跨重启缓存收益
    重评：当需要保留历史缓存或提供缓存查询接口时
    """

    def __init__(self, cache_dir, max_size=None, expiration_time=3600) -> None:
        self.cache = Cache(cache_dir)
        if len(self.cache) > 0:
            self.cache.clear()
        self.lock = asyncio.Lock()
        self.max_size = max_size
        self.expiration_time = expiration_time

    async def get(self, key, lock: asyncio.Lock | None = None):
        """读取缓存值。

        契约：输入 `key` 与可选锁；输出命中值或 `CACHE_MISS`。
        失败语义：底层 I/O 或反序列化异常向上抛出。
        """
        if not lock:
            async with self.lock:
                return await asyncio.to_thread(self._get, key)
        else:
            return await asyncio.to_thread(self._get, key)

    def _get(self, key):
        item = self.cache.get(key, default=None)
        if item:
            if time.time() - item["time"] < self.expiration_time:
                self.cache.touch(key)
                return pickle.loads(item["value"]) if isinstance(item["value"], bytes) else item["value"]
            logger.info(f"Cache item for key '{key}' has expired and will be deleted.")
            self.cache.delete(key)
        return CACHE_MISS

    async def set(self, key, value, lock: asyncio.Lock | None = None) -> None:
        """写入缓存值。

        契约：输入 `key`/`value` 与可选锁；无返回值；允许覆盖。
        """
        if not lock:
            async with self.lock:
                await self._set(key, value)
        else:
            await self._set(key, value)

    async def _set(self, key, value) -> None:
        if self.max_size and len(self.cache) >= self.max_size:
            await asyncio.to_thread(self.cache.cull)
        item = {"value": pickle.dumps(value) if not isinstance(value, str | bytes) else value, "time": time.time()}
        await asyncio.to_thread(self.cache.set, key, item)

    async def delete(self, key, lock: asyncio.Lock | None = None) -> None:
        """删除缓存项。"""
        if not lock:
            async with self.lock:
                await self._delete(key)
        else:
            await self._delete(key)

    async def _delete(self, key) -> None:
        await asyncio.to_thread(self.cache.delete, key)

    async def clear(self, lock: asyncio.Lock | None = None) -> None:
        """清空缓存。"""
        if not lock:
            async with self.lock:
                await self._clear()
        else:
            await self._clear()

    async def _clear(self) -> None:
        await asyncio.to_thread(self.cache.clear)

    async def upsert(self, key, value, lock: asyncio.Lock | None = None) -> None:
        """插入或更新缓存项。

        契约：若旧值与新值均为 `dict`，则合并后写回。
        """
        if not lock:
            async with self.lock:
                await self._upsert(key, value)
        else:
            await self._upsert(key, value)

    async def _upsert(self, key, value) -> None:
        existing_value = await asyncio.to_thread(self._get, key)
        if existing_value is not CACHE_MISS and isinstance(existing_value, dict) and isinstance(value, dict):
            existing_value.update(value)
            value = existing_value
        await self.set(key, value)

    async def contains(self, key) -> bool:
        """判断键是否存在于缓存。"""
        return await asyncio.to_thread(self.cache.__contains__, key)

    async def teardown(self) -> None:
        """释放缓存资源并清空磁盘内容。"""
        self.cache.clear(retry=True)
