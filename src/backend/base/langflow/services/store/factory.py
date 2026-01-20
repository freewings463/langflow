"""
模块名称：Store 服务工厂

本模块提供 `StoreService` 的工厂注册与实例化入口，主要用于服务容器统一创建 Store 服务。
主要功能包括：
- 通过工厂模式构造 `StoreService` 并注入配置依赖。

关键组件：`StoreServiceFactory`。
设计背景：服务层需要统一的创建入口以控制依赖注入。
使用场景：应用启动或服务注册阶段。
注意事项：工厂仅负责实例化，不承担配置校验。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from typing_extensions import override

from langflow.services.factory import ServiceFactory
from langflow.services.store.service import StoreService

if TYPE_CHECKING:
    from lfx.services.settings.service import SettingsService


class StoreServiceFactory(ServiceFactory):
    """Store 服务工厂。

    契约：`create` 接收 `settings_service`，返回 `StoreService` 实例；副作用：无。
    关键路径：通过 `StoreService(settings_service)` 完成实例化。
    决策：使用工厂模式注册服务
    问题：需要统一服务构建入口
    方案：继承 `ServiceFactory` 并覆盖 `create`
    代价：增加一层间接调用
    重评：若服务容器改为直接构造
    """

    def __init__(self) -> None:
        super().__init__(StoreService)

    @override
    def create(self, settings_service: SettingsService):
        """构造 Store 服务实例。

        契约：输入 `settings_service`，输出 `StoreService`；副作用：无。
        关键路径：直接调用 `StoreService` 构造函数。
        决策：在工厂层不做额外逻辑
        问题：避免在创建阶段引入副作用
        方案：仅实例化与返回
        代价：配置错误会在运行期暴露
        重评：若需在启动时强制配置校验
        """
        return StoreService(settings_service)
