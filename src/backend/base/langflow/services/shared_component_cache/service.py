"""
模块名称：共享组件缓存服务

本模块提供跨组件共享的内存缓存服务，主要用于减少重复计算与资源加载。主要功能包括：
- 继承 `ThreadingInMemoryCache` 提供线程安全缓存

关键组件：
- SharedComponentCacheService

设计背景：多个组件之间需要共享缓存以避免重复开销。
注意事项：缓存为进程内存级别，进程重启会清空。
"""

from langflow.services.cache.service import ThreadingInMemoryCache


class SharedComponentCacheService(ThreadingInMemoryCache):
    """共享组件缓存服务。

    契约：基于 `ThreadingInMemoryCache`，具备过期控制与线程安全访问。
    副作用：占用进程内存；淘汰策略由父类实现。
    失败语义：父类缓存操作异常会向上抛出。
    """

    name = "shared_component_cache_service"
