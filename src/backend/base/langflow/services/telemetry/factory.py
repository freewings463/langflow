"""
模块名称：Telemetry 服务工厂

本模块提供 `TelemetryService` 的工厂实现，统一服务构造方式。
主要功能：基于 `ServiceFactory` 创建 `TelemetryService` 实例。
设计背景：服务注册与依赖注入的标准化入口。
注意事项：创建依赖 `SettingsService`，未提供则无法使用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from typing_extensions import override

from langflow.services.factory import ServiceFactory
from langflow.services.telemetry.service import TelemetryService

if TYPE_CHECKING:
    from lfx.services.settings.service import SettingsService


class TelemetryServiceFactory(ServiceFactory):
    """TelemetryService 工厂类。"""

    def __init__(self) -> None:
        super().__init__(TelemetryService)

    @override
    def create(self, settings_service: SettingsService):
        """创建 TelemetryService 实例并注入配置服务。"""
        return TelemetryService(settings_service)
