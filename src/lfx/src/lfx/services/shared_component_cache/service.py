"""
模块名称：共享组件缓存服务实现

本模块提供跨组件可复用的内存缓存服务实现。
主要功能包括：
- 继承线程安全内存缓存能力
- 作为共享缓存服务被其他组件复用

设计背景：多个组件需要共享缓存而不重复实例化。
注意事项：缓存生命周期由服务工厂配置的过期时间控制。
"""

from lfx.services.cache.service import ThreadingInMemoryCache


class SharedComponentCacheService(ThreadingInMemoryCache):
    """跨组件共享的内存缓存服务。"""

    name = "shared_component_cache_service"
