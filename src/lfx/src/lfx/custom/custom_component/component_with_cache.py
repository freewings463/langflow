"""模块名称：带共享缓存的组件基类

本模块提供带共享缓存服务的组件基类，便于在组件间复用缓存资源。
主要功能包括：初始化共享缓存服务并注入组件实例。

关键组件：
- `ComponentWithCache`：集成共享缓存的组件基类

设计背景：避免重复构建缓存实例，统一缓存策略。
注意事项：缓存服务由依赖注入提供，需确保服务可用。
"""

from lfx.custom.custom_component.component import Component
from lfx.services.deps import get_shared_component_cache_service


class ComponentWithCache(Component):
    """带共享缓存的组件基类。

    契约：输入组件初始化参数；输出无；副作用：获取共享缓存服务；
    失败语义：缓存服务不可用时抛异常。
    关键路径：1) 初始化父类 2) 获取共享缓存服务。
    决策：在构造函数中注入缓存服务
    问题：需要统一缓存生命周期
    方案：通过依赖服务获取共享实例
    代价：构造函数对服务可用性敏感
    重评：当缓存服务改为惰性加载时延迟获取
    """

    def __init__(self, **data) -> None:
        """初始化组件并注入共享缓存服务。

        契约：输入关键字参数；输出无；副作用：设置 `_shared_component_cache`；
        失败语义：依赖服务不可用时抛异常。
        关键路径：1) 调用父类构造 2) 获取共享缓存服务。
        决策：缓存服务在初始化时获取
        问题：确保组件运行时缓存可用
        方案：构造函数直接注入
        代价：初始化时依赖服务必须就绪
        重评：当依赖服务支持延迟初始化时改为按需获取
        """
        super().__init__(**data)
        self._shared_component_cache = get_shared_component_cache_service()
