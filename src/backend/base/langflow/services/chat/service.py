"""
模块名称：聊天缓存服务

本模块实现聊天缓存的读写与清理，并兼容同步/异步缓存服务。主要功能包括：
- `ChatService`：提供 `set_cache`/`get_cache`/`clear_cache`
- 通过 `get_cache_service` 获取底层缓存实现

关键组件：
- `ChatService`

设计背景：统一聊天缓存访问，屏蔽底层缓存实现差异。
注意事项：同步缓存使用 `asyncio.to_thread` 包装以避免阻塞事件循环。
"""

import asyncio
from collections import defaultdict
from threading import RLock
from typing import Any

from langflow.services.base import Service
from langflow.services.cache.base import AsyncBaseCacheService, CacheService
from langflow.services.deps import get_cache_service


class ChatService(Service):
    """聊天缓存服务。

    契约：`key` 为缓存键，兼容 `AsyncBaseCacheService` 与同步 `CacheService`。
    副作用：读写底层缓存；失败语义：底层异常向上传播。
    关键路径（三步）：1) 选择缓存实现 2) 获取锁 3) 执行读写
    决策：同时支持同步与异步缓存实现
    问题：部署环境可能使用不同缓存后端
    方案：运行时判断实现类型并选择调用路径
    代价：引入分支与线程切换开销
    重评：当所有缓存实现统一为异步时
    """

    name = "chat_service"

    def __init__(self) -> None:
        """初始化缓存锁与底层缓存服务引用。

        契约：为每个 `key` 维护独立锁映射，并绑定缓存服务实现。
        副作用：创建 `defaultdict` 锁池；失败语义：依赖 `get_cache_service` 的异常传播。
        关键路径（三步）：1) 初始化异步锁池 2) 初始化同步锁池 3) 绑定缓存服务
        决策：为每个 `key` 维护独立锁
        问题：并发读写同一键需串行化
        方案：使用 `defaultdict` 生成 `asyncio.Lock` 与 `RLock`
        代价：锁对象数量与键数量线性增长
        重评：当锁管理成为性能瓶颈或需集中管理时
        """
        self.async_cache_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._sync_cache_locks: dict[str, RLock] = defaultdict(RLock)
        self.cache_service: CacheService | AsyncBaseCacheService = get_cache_service()

    async def set_cache(self, key: str, data: Any, lock: asyncio.Lock | None = None) -> bool:
        """写入缓存并返回写入状态。

        契约：`key` 为缓存键；返回写入后是否存在的 `bool`。
        副作用：写入底层缓存；失败语义：底层异常向上传播。
        关键路径（三步）：1) 构造 `result_dict` 2) 选择调用路径 3) 校验存在性
        决策：缓存值包装为 `{"result": data, "type": type(data)}`
        问题：需要保留数据类型便于后续解析
        方案：写入时附带 `type` 元信息
        代价：缓存体积增加
        重评：当下游不再需要类型信息时
        """
        result_dict = {
            "result": data,
            "type": type(data),
        }
        if isinstance(self.cache_service, AsyncBaseCacheService):
            await self.cache_service.upsert(str(key), result_dict, lock=lock or self.async_cache_locks[key])
            return await self.cache_service.contains(key)
        await asyncio.to_thread(
            self.cache_service.upsert, str(key), result_dict, lock=lock or self._sync_cache_locks[key]
        )
        return key in self.cache_service

    async def get_cache(self, key: str, lock: asyncio.Lock | None = None) -> Any:
        """读取缓存数据。

        契约：`key` 为缓存键；返回值由底层缓存实现决定。
        副作用：读取底层缓存；失败语义：底层异常向上传播。
        关键路径（三步）：1) 选择缓存实现 2) 选择 `lock` 3) 读取并返回
        决策：同步缓存通过 `asyncio.to_thread` 调用
        问题：同步缓存调用会阻塞事件循环
        方案：将同步调用放入线程池
        代价：线程切换带来额外开销
        重评：当同步缓存被异步实现替代时
        """
        if isinstance(self.cache_service, AsyncBaseCacheService):
            return await self.cache_service.get(key, lock=lock or self.async_cache_locks[key])
        return await asyncio.to_thread(self.cache_service.get, key, lock=lock or self._sync_cache_locks[key])

    async def clear_cache(self, key: str, lock: asyncio.Lock | None = None) -> None:
        """清理指定缓存键。

        契约：`key` 为缓存键；返回 `None`。
        副作用：删除底层缓存；失败语义：底层异常向上传播。
        关键路径（三步）：1) 选择缓存实现 2) 选择 `lock` 3) 删除条目
        决策：清理接口统一返回 `None`
        问题：不同缓存实现可能返回删除结果
        方案：忽略底层返回值以统一接口
        代价：无法感知实际删除状态
        重评：当需要删除结果或计数统计时
        """
        if isinstance(self.cache_service, AsyncBaseCacheService):
            return await self.cache_service.delete(key, lock=lock or self.async_cache_locks[key])
        return await asyncio.to_thread(self.cache_service.delete, key, lock=lock or self._sync_cache_locks[key])
