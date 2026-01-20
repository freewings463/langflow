"""
模块名称：状态服务工厂

本模块提供状态服务的工厂实现，用于依赖注入与统一创建。主要功能包括：
- 提供 `StateServiceFactory` 作为创建入口
- 统一绑定默认的状态服务实现

关键组件：
- `StateServiceFactory`

使用场景：服务容器需要按配置创建状态服务实例。
设计背景：通过工厂隔离具体实现，便于未来替换为持久化存储。
注意事项：当前仅返回 `InMemoryStateService`。
"""

from lfx.services.settings.service import SettingsService
from typing_extensions import override

from langflow.services.factory import ServiceFactory
from langflow.services.state.service import InMemoryStateService


class StateServiceFactory(ServiceFactory):
    """状态服务工厂，负责实例化具体实现。

    契约：`create` 返回 `StateService` 实例；工厂自身不持有状态。
    关键职责：提供统一创建入口并屏蔽实现细节。
    失败语义：构造失败时抛出异常。
    决策：工厂固定绑定默认实现
    问题：多实现并存会增加调用方判断成本
    方案：在工厂层统一选择默认实现
    代价：切换实现需调整工厂配置
    重评：当需要多后端并存时改为配置驱动
    """

    def __init__(self) -> None:
        """初始化工厂并绑定默认实现。

        契约：默认实现为 `InMemoryStateService`；无返回值。
        副作用：调用父类构造函数注册实现。
        失败语义：不抛异常。
        关键路径：1) 绑定默认实现 2) 注册到父类。
        决策：在构造阶段绑定默认实现
        问题：延迟绑定会增加运行期分支
        方案：在工厂初始化时固定实现
        代价：运行中切换实现需重建工厂
        重评：当需要热切换实现时改为惰性选择
        """
        super().__init__(InMemoryStateService)

    @override
    def create(self, settings_service: SettingsService):
        """创建状态服务实例。

        契约：接收 `settings_service`，返回 `InMemoryStateService`。
        副作用：无外部 `I/O`。
        失败语义：构造失败时抛异常。
        关键路径：1) 注入配置服务 2) 返回实例。
        决策：工厂直接返回内存实现
        问题：当前无持久化状态存储
        方案：默认选择内存实现以降低复杂度
        代价：进程重启后状态丢失
        重评：当需要持久化或分布式时切换实现
        """
        return InMemoryStateService(
            settings_service,
        )
