"""
模块名称：服务注册装饰器

本模块提供服务注册装饰器，便于插件式服务自注册。
主要功能包括：
- 通过装饰器将服务类注册到管理器
- 支持覆盖已有注册

设计背景：简化插件式服务的注册流程。
注意事项：Settings 服务不可通过装饰器覆盖。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

from lfx.log.logger import logger

if TYPE_CHECKING:
    from lfx.services.base import Service
    from lfx.services.schema import ServiceType

ServiceT = TypeVar("ServiceT", bound="Service")


def register_service(service_type: ServiceType, *, override: bool = True):
    """注册服务类的装饰器。

    契约：注册成功后返回原类，不改变其行为。
    """

    def decorator(service_class: type[ServiceT]) -> type[ServiceT]:
        """注册服务类并返回原类。"""
        try:
            from lfx.services.manager import get_service_manager

            service_manager = get_service_manager()
            service_manager.register_service_class(service_type, service_class, override=override)
            logger.debug(f"Registered service via decorator: {service_type.value} -> {service_class.__name__}")
        except ValueError:
            # 注意：Settings 服务保护逻辑直接抛出。
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Failed to register service {service_type.value} from decorator: {exc}")

        return service_class

    return decorator
