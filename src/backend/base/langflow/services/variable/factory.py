"""
模块名称：变量服务工厂

本模块提供变量服务的工厂创建模式实现，根据配置动态创建不同类型的变量服务。
主要功能包括：
- 根据设置动态创建数据库或Kubernetes变量服务
- 管理变量服务的实例化过程

关键组件：
- `VariableServiceFactory`

设计背景：支持多种变量存储后端，通过工厂模式统一创建入口。
注意事项：根据settings.variable_store配置决定创建哪种服务实现。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from typing_extensions import override

from langflow.services.factory import ServiceFactory
from langflow.services.variable.service import DatabaseVariableService, VariableService

if TYPE_CHECKING:
    from lfx.services.settings.service import SettingsService


class VariableServiceFactory(ServiceFactory):
    def __init__(self) -> None:
        super().__init__(VariableService)

    @override
    def create(self, settings_service: SettingsService):
        """根据设置创建变量服务实例。

        契约：根据settings_service的配置创建相应的变量服务实现。
        副作用：可能导入Kubernetes服务实现。
        失败语义：配置无效时会抛出异常。
        
        决策：使用工厂模式动态选择变量存储后端
        问题：需要支持多种变量存储方式（数据库、Kubernetes等）
        方案：根据配置动态创建相应服务实例
        代价：增加了初始化时的条件判断逻辑
        重评：当需要支持更多存储后端时
        """
        # here you would have logic to create and configure a VariableService
        # based on the settings_service

        if settings_service.settings.variable_store == "kubernetes":
            # Keep it here to avoid import errors
            from langflow.services.variable.kubernetes import KubernetesSecretService

            return KubernetesSecretService(settings_service)
        return DatabaseVariableService(settings_service)
