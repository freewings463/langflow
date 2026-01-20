"""
模块名称：服务工厂基类

本模块定义了服务工厂的抽象基类和相关工具函数，用于创建和管理服务实例。
主要功能包括：
- 服务工厂基类定义
- 服务类型推断功能
- 服务自动导入和缓存功能

关键组件：
- `ServiceFactory`：服务工厂基类
- `infer_service_types`：服务类型推断函数
- `import_all_services_into_a_dict`：服务导入函数

设计背景：提供统一的服务创建和管理机制。
注意事项：工厂类需要正确配置依赖关系。
"""

import importlib
import inspect
from typing import get_type_hints

from cachetools import LRUCache, cached
from lfx.log.logger import logger

from langflow.services.base import Service
from langflow.services.schema import ServiceType


class ServiceFactory:
    def __init__(
        self,
        service_class: type[Service] | None = None,
    ) -> None:
        """初始化服务工厂。

        契约：创建服务工厂实例并推断依赖。
        副作用：设置服务类和依赖项。
        失败语义：如果 service_class 为 None 则抛出 ValueError。
        
        决策：要求 service_class 必须提供
        问题：避免在运行时才发现缺少服务类
        方案：在初始化时验证参数
        代价：初始化时增加验证开销
        重评：如果需要支持延迟配置则调整验证时机
        """
        if service_class is None:
            msg = "service_class is required"
            raise ValueError(msg)
        self.service_class = service_class
        self.dependencies = infer_service_types(self, import_all_services_into_a_dict())

    def create(self, *args, **kwargs) -> "Service":
        """创建服务实例。

        契约：根据提供的参数创建服务实例。
        副作用：实例化服务类。
        失败语义：如果参数不正确则抛出异常。
        """
        return self.service_class(*args, **kwargs)


def hash_factory(factory: ServiceFactory) -> str:
    """生成工厂的哈希值。

    契约：返回工厂服务类名称作为哈希值。
    副作用：无。
    失败语义：不抛出异常。
    """
    return factory.service_class.__name__


def hash_dict(d: dict) -> str:
    """生成字典的哈希值。

    契约：返回字典的字符串表示作为哈希值。
    副作用：无。
    失败语义：不抛出异常。
    """
    return str(d)


def hash_infer_service_types_args(factory: ServiceFactory, available_services=None) -> str:
    """生成 infer_service_types 函数参数的哈希值。

    契约：返回组合的哈希字符串。
    副作用：无。
    失败语义：不抛出异常。
    """
    factory_hash = hash_factory(factory)
    services_hash = hash_dict(available_services)
    return f"{factory_hash}_{services_hash}"


@cached(cache=LRUCache(maxsize=10), key=hash_infer_service_types_args)
def infer_service_types(factory: ServiceFactory, available_services=None) -> list["ServiceType"]:
    """推断工厂的依赖服务类型。

    契约：返回依赖服务类型的列表。
    副作用：无。
    失败语义：如果找不到匹配的 ServiceType 则抛出 ValueError。
    """
    create_method = factory.create

    type_hints = get_type_hints(create_method, globalns=available_services)

    service_types = []
    for param_name, param_type in type_hints.items():
        # Skip the return type if it's included in type hints
        if param_name == "return":
            continue

        # Convert the type to the expected enum format directly without appending "_SERVICE"
        type_name = param_type.__name__.upper().replace("SERVICE", "_SERVICE")

        try:
            # Attempt to find a matching enum value
            service_type = ServiceType[type_name]
            service_types.append(service_type)
        except KeyError as e:
            msg = f"No matching ServiceType for parameter type: {param_type.__name__}"
            raise ValueError(msg) from e
    return service_types


@cached(cache=LRUCache(maxsize=1))
def import_all_services_into_a_dict():
    """导入所有服务到字典中。

    契约：返回包含所有服务类的字典。
    副作用：导入多个服务模块。
    失败语义：如果导入失败则抛出 RuntimeError。
    
    决策：使用缓存避免重复导入
    问题：多次导入相同服务模块会消耗性能
    方案：使用 LRUCache 缓存结果
    代价：占用内存存储缓存
    重评：如果内存成为瓶颈则调整缓存策略
    """
    # Services are all in langflow.services.{service_name}.service
    # and are subclass of Service
    # We want to import all of them and put them in a dict
    # to use as globals
    from langflow.services.base import Service

    services = {}
    for service_type in ServiceType:
        try:
            service_name = ServiceType(service_type).value.replace("_service", "")

            # Special handling for mcp_composer which is now in lfx module
            if service_name == "mcp_composer":
                module_name = f"lfx.services.{service_name}.service"
            else:
                module_name = f"langflow.services.{service_name}.service"

            module = importlib.import_module(module_name)
            services.update(
                {
                    name: obj
                    for name, obj in inspect.getmembers(module, inspect.isclass)
                    if isinstance(obj, type) and issubclass(obj, Service) and obj is not Service
                }
            )
        except Exception as exc:
            logger.exception(exc)
            msg = "Could not initialize services. Please check your settings."
            raise RuntimeError(msg) from exc
    # Import settings service from lfx
    from lfx.services.settings.service import SettingsService

    services["SettingsService"] = SettingsService
    return services