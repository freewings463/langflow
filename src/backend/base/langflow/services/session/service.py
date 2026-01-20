"""
模块名称：Session 缓存服务

本模块提供会话级缓存的读取、生成与清理逻辑，用于复用流程图与运行产物。
主要功能：
- 从缓存加载或构建图与产物
- 基于输入数据生成稳定缓存键
- 更新与清理会话缓存
设计背景：减少重复图构建开销并隔离会话状态。
注意事项：同步/异步缓存实现需分支处理。
"""

import asyncio
from typing import TYPE_CHECKING

from lfx.services.cache.utils import CacheMiss

from langflow.services.base import Service
from langflow.services.cache.base import AsyncBaseCacheService
from langflow.services.session.utils import compute_dict_hash, session_id_generator

if TYPE_CHECKING:
    from langflow.services.cache.base import CacheService


class SessionService(Service):
    """会话缓存服务。"""

    name = "session_service"

    def __init__(self, cache_service) -> None:
        self.cache_service: CacheService | AsyncBaseCacheService = cache_service

    async def load_session(self, key, flow_id: str, data_graph: dict | None = None):
        """加载或构建会话缓存。

        契约：
        - 输入：`key`、`flow_id`、`data_graph`
        - 输出：缓存命中时返回 `(graph, artifacts)`；未命中可能返回 `(None, None)`
        - 副作用：缓存未命中时会构建图并写入缓存
        - 失败语义：构建图失败抛异常
        """
        # 实现：优先从缓存读取。
        if isinstance(self.cache_service, AsyncBaseCacheService):
            value = await self.cache_service.get(key)
        else:
            value = await asyncio.to_thread(self.cache_service.get, key)
        if not isinstance(value, CacheMiss):
            return value

        if key is None:
            key = self.generate_key(session_id=None, data_graph=data_graph)
        if data_graph is None:
            return None, None
        # 实现：缓存未命中时构建图并写入缓存。
        from lfx.graph.graph.base import Graph

        graph = Graph.from_payload(data_graph, flow_id=flow_id)
        artifacts: dict = {}
        await self.cache_service.set(key, (graph, artifacts))

        return graph, artifacts

    @staticmethod
    def build_key(session_id, data_graph) -> str:
        """基于 session 与图数据生成稳定键。"""
        json_hash = compute_dict_hash(data_graph)
        return f"{session_id}{':' if session_id else ''}{json_hash}"

    def generate_key(self, session_id, data_graph):
        """生成缓存键，缺省时自动生成会话 ID。"""
        # 实现：基于 JSON 哈希与会话 ID 生成唯一键。
        if session_id is None:
            # 注意：生成 5 位会话 ID 以缩短键长度。
            session_id = session_id_generator()
        return self.build_key(session_id, data_graph=data_graph)

    async def update_session(self, session_id, value) -> None:
        """更新会话缓存内容。"""
        if isinstance(self.cache_service, AsyncBaseCacheService):
            await self.cache_service.set(session_id, value)
        else:
            await asyncio.to_thread(self.cache_service.set, session_id, value)

    async def clear_session(self, session_id) -> None:
        """清理指定会话缓存。"""
        if isinstance(self.cache_service, AsyncBaseCacheService):
            await self.cache_service.delete(session_id)
        else:
            await asyncio.to_thread(self.cache_service.delete, session_id)
