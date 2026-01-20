"""
模块名称：事务服务工厂

本模块提供事务服务的创建工厂，主要用于依赖注入与配置绑定。主要功能包括：
- 通过 `SettingsService` 初始化 `TransactionService`

关键组件：
- TransactionServiceFactory

设计背景：将服务实例化与配置读取解耦，便于统一管理。
注意事项：事务开关由 `settings_service` 控制，影响全局记录行为。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langflow.services.factory import ServiceFactory
from langflow.services.transaction.service import TransactionService

if TYPE_CHECKING:
    from langflow.services.settings.service import SettingsService


class TransactionServiceFactory(ServiceFactory):
    """事务服务工厂。

    契约：`create` 需要 `SettingsService`；返回 `TransactionService` 实例。
    副作用：无。
    失败语义：配置缺失时由 `SettingsService` 抛出异常。
    """

    def __init__(self):
        super().__init__(TransactionService)

    def create(self, settings_service: SettingsService):
        """创建事务服务实例。

        关键路径（三步）：
        1) 接收 `SettingsService` 作为配置源。
        2) 构造 `TransactionService`。
        3) 返回服务实例。
        """
        return TransactionService(settings_service)
