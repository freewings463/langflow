"""
模块名称：缓存服务实现

本模块提供基于内存的缓存实现，支持过期与 LRU 淘汰策略。
主要功能：
- 线程安全的内存缓存；
- 过期时间与最大容量控制；
- 提供 get/set/upsert 等常见操作。

设计背景：为 lfx 提供轻量级默认缓存实现。
注意事项：仅内存级缓存，不适合跨进程共享。
"""

import pickle
import threading
import time
from collections import OrderedDict
from typing import Generic, Union

from lfx.services.cache.base import CacheService, LockType
from lfx.services.cache.utils import CACHE_MISS


class ThreadingInMemoryCache(CacheService, Generic[LockType]):
    """A simple in-memory cache using an OrderedDict.

    This cache supports setting a maximum size and expiration time for cached items.
    When the cache is full, it uses a Least Recently Used (LRU) eviction policy.
    Thread-safe using a threading Lock.

    Attributes:
        max_size (int, optional): Maximum number of items to store in the cache.
        expiration_time (int, optional): Time in seconds after which a cached item expires. Default is 1 hour.

    Example:
        cache = ThreadingInMemoryCache(max_size=3, expiration_time=5)

        # 示例：写入缓存值。
        cache.set("a", 1)
        cache.set("b", 2)
        cache["c"] = 3

        # 示例：读取缓存值。
        a = cache.get("a")
        b = cache["b"]
    """

    def __init__(self, max_size=None, expiration_time=60 * 60) -> None:
        """初始化内存缓存

        契约：配置最大容量与过期时间；默认过期为 1 小时。
        """
        self._cache: OrderedDict = OrderedDict()
        self._lock = threading.RLock()
        self.max_size = max_size
        self.expiration_time = expiration_time

    def get(self, key, lock: Union[threading.Lock, None] = None):  # noqa: UP007
        """获取缓存值

        契约：未命中或过期返回 CACHE_MISS。
        """
        with lock or self._lock:
            return self._get_without_lock(key)

    def _get_without_lock(self, key):
        """无锁获取缓存值（内部使用）。"""
        if item := self._cache.get(key):
            if self.expiration_time is None or time.time() - item["time"] < self.expiration_time:
                # 注意：命中后移动到队尾以保持 LRU 顺序。
                self._cache.move_to_end(key)
                # 注意：若为字节则反序列化。
                return pickle.loads(item["value"]) if isinstance(item["value"], bytes) else item["value"]  # noqa: S301
            self.delete(key)
        return CACHE_MISS

    def set(self, key, value, lock: Union[threading.Lock, None] = None) -> None:  # noqa: UP007
        """写入缓存值

        契约：若超出容量则淘汰最久未使用项。
        """
        with lock or self._lock:
            if key in self._cache:
                # 注意：先删除再写入以更新 LRU 顺序。
                self.delete(key)
            elif self.max_size and len(self._cache) >= self.max_size:
                # 注意：移除最久未使用项。
                self._cache.popitem(last=False)
            # 注意：本地写入，保持 Redis 类似行为（可扩展）。

            self._cache[key] = {"value": value, "time": time.time()}

    def upsert(self, key, value, lock: Union[threading.Lock, None] = None) -> None:  # noqa: UP007
        """插入或更新缓存值

        契约：若旧值和新值均为 dict，则合并更新。
        """
        with lock or self._lock:
            existing_value = self._get_without_lock(key)
            if existing_value is not CACHE_MISS and isinstance(existing_value, dict) and isinstance(value, dict):
                existing_value.update(value)
                value = existing_value

            self.set(key, value)

    def get_or_set(self, key, value, lock: Union[threading.Lock, None] = None):  # noqa: UP007
        """获取或写入缓存值

        契约：若不存在则写入并返回。
        """
        with lock or self._lock:
            if key in self._cache:
                return self.get(key)
            self.set(key, value)
            return value

    def delete(self, key, lock: Union[threading.Lock, None] = None) -> None:  # noqa: UP007
        """删除指定 key。"""
        with lock or self._lock:
            self._cache.pop(key, None)

    def clear(self, lock: Union[threading.Lock, None] = None) -> None:  # noqa: UP007
        """清空缓存。"""
        with lock or self._lock:
            self._cache.clear()

    def contains(self, key) -> bool:
        """判断 key 是否存在。"""
        return key in self._cache

    def __contains__(self, key) -> bool:
        """`in` 操作支持。"""
        return self.contains(key)

    def __getitem__(self, key):
        """`cache[key]` 读取接口。"""
        return self.get(key)

    def __setitem__(self, key, value) -> None:
        """`cache[key] = value` 写入接口。"""
        self.set(key, value)

    def __delitem__(self, key) -> None:
        """`del cache[key]` 删除接口。"""
        self.delete(key)

    def __len__(self) -> int:
        """返回缓存条目数。"""
        return len(self._cache)

    def __repr__(self) -> str:
        """返回缓存实例的字符串表示。"""
        return f"ThreadingInMemoryCache(max_size={self.max_size}, expiration_time={self.expiration_time})"
