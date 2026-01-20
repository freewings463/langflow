"""
模块名称：TracingService 工厂

本模块提供 tracing 服务的工厂封装。
主要功能包括：
- 通过 ServiceFactory 创建 `TracingService`

关键组件：
- `TracingServiceFactory`

设计背景：统一服务创建入口，便于依赖注入。
注意事项：仅负责实例化，不承载业务逻辑。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from typing_extensions import override

from langflow.services.factory import ServiceFactory
from langflow.services.tracing.service import TracingService

if TYPE_CHECKING:
    from lfx.services.settings.service import SettingsService


class TracingServiceFactory(ServiceFactory):
    def __init__(self) -> None:
        super().__init__(TracingService)

    @override
    def create(self, settings_service: SettingsService):
        """创建 TracingService 实例。"""
        return TracingService(settings_service)
