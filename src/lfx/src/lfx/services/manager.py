"""
模块名称：服务管理器

本模块提供可插拔的服务发现与创建机制。
主要功能包括：
- 通过工厂或插件注册服务
- 支持 entry points / 配置文件 / 装饰器注册
- 管理服务依赖与生命周期

设计背景：服务实现多样，需统一管理与可扩展发现机制。
注意事项：Settings 服务不可被插件覆盖。
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from lfx.log.logger import logger
from lfx.services.schema import ServiceType
from lfx.utils.concurrency import KeyedMemoryLockManager

if TYPE_CHECKING:
    from lfx.services.base import Service
    from lfx.services.factory import ServiceFactory


class NoFactoryRegisteredError(Exception):
    """当服务类型未注册工厂时抛出。"""


class NoServiceRegisteredError(Exception):
    """当服务类型未注册服务或工厂时抛出。"""


class ServiceManager:
    """可插拔服务管理器。"""

    def __init__(self) -> None:
        """初始化服务与工厂注册表。"""
        self.services: dict[str, Service] = {}
        self.factories: dict[str, ServiceFactory] = {}
        self.service_classes: dict[ServiceType, type[Service]] = {}  # 注意：直接服务类注册表
        self._lock = threading.RLock()
        self.keyed_lock = KeyedMemoryLockManager()
        self.factory_registered = False
        self._plugins_discovered = False

        # 注意：Settings 服务必须始终注册。
        from lfx.services.settings.factory import SettingsServiceFactory

        self.register_factory(SettingsServiceFactory())

    def register_factories(self, factories: list[ServiceFactory] | None = None) -> None:
        """注册全部可用服务工厂。"""
        if factories is None:
            return
        for factory in factories:
            try:
                self.register_factory(factory)
            except Exception:  # noqa: BLE001
                logger.exception(f"Error initializing {factory}")
        self.set_factory_registered()

    def are_factories_registered(self) -> bool:
        """检查工厂是否已注册。"""
        return self.factory_registered

    def set_factory_registered(self) -> None:
        """设置工厂注册完成标记。"""
        self.factory_registered = True

    def register_service_class(
        self,
        service_type: ServiceType,
        service_class: type[Service],
        *,
        override: bool = True,
    ) -> None:
        """直接注册服务类（无需工厂）。

        契约：支持覆盖已有注册。
        失败语义：注册 Settings 服务将抛 `ValueError`。
        """
        # 注意：Settings 服务不可被插件覆盖。
        if service_type == ServiceType.SETTINGS_SERVICE:
            msg = "Settings service cannot be registered via plugins. It is always created using the built-in factory."
            logger.warning(msg)
            raise ValueError(msg)

        if service_type in self.service_classes and not override:
            logger.warning(f"Service {service_type.value} already registered. Use override=True to replace it.")
            return

        if service_type in self.service_classes:
            logger.debug(f"Overriding service registration for {service_type.value}")

        self.service_classes[service_type] = service_class
        logger.debug(f"Registered service class: {service_type.value} -> {service_class.__name__}")

    def register_factory(
        self,
        service_factory: ServiceFactory,
    ) -> None:
        """注册服务工厂并记录依赖。"""
        service_name = service_factory.service_class.name
        self.factories[service_name] = service_factory

    def get(self, service_name: ServiceType, default: ServiceFactory | None = None) -> Service:
        """获取或创建指定服务实例。"""
        with self.keyed_lock.lock(service_name):
            if service_name not in self.services:
                self._create_service(service_name, default)
            return self.services[service_name]

    def _create_service(self, service_name: ServiceType, default: ServiceFactory | None = None) -> None:
        """创建服务实例并处理依赖。"""
        logger.debug(f"Create service {service_name}")

        # 注意：Settings 服务只能通过工厂创建。
        if service_name == ServiceType.SETTINGS_SERVICE:
            self._create_service_from_factory(service_name, default)
            return

        # 注意：首次创建前触发插件发现。
        if not self._plugins_discovered:
            # 实现：从 settings 服务获取配置目录（若可用）。
            config_dir = None
            if ServiceType.SETTINGS_SERVICE in self.services:
                settings_service = self.services[ServiceType.SETTINGS_SERVICE]
                if hasattr(settings_service, "settings") and settings_service.settings.config_dir:
                    config_dir = Path(settings_service.settings.config_dir)

            self.discover_plugins(config_dir)

        # 实现：优先使用直接注册的服务类。
        if service_name in self.service_classes:
            self._create_service_from_class(service_name)
            return

        # 实现：回退到工厂创建（旧系统）。
        self._create_service_from_factory(service_name, default)

    def _create_service_from_class(self, service_name: ServiceType) -> None:
        """从已注册服务类创建实例（插件系统）。"""
        service_class = self.service_classes[service_name]
        logger.debug(f"Creating service from class: {service_name.value} -> {service_class.__name__}")

        # 实现：解析 __init__ 获取依赖。
        init_signature = inspect.signature(service_class.__init__)
        dependencies = {}

        for param_name, param in init_signature.parameters.items():
            if param_name == "self":
                continue

            # 实现：优先从类型注解解析依赖。
            dependency_type = None
            if param.annotation != inspect.Parameter.empty:
                dependency_type = self._resolve_service_type_from_annotation(param.annotation)

            # 实现：类型注解失败时尝试从参数名解析。
            if not dependency_type:
                try:
                    dependency_type = ServiceType(param_name)
                except ValueError:
                    # 注意：无法解析且无默认值时，实例化可能失败。
                    if param.default == inspect.Parameter.empty:
                        # 注意：无默认值无法解析，实例化可能失败。
                        pass
                    continue

            if dependency_type:
                # 实现：递归创建依赖服务。
                if dependency_type not in self.services:
                    self._create_service(dependency_type)
                dependencies[param_name] = self.services[dependency_type]

        # 实现：创建服务实例并写入缓存。
        try:
            service_instance = service_class(**dependencies)
            # 注意：由服务自身控制 ready 状态。
            self.services[service_name] = service_instance
            logger.debug(f"Service created successfully: {service_name.value}")
        except Exception as exc:
            logger.exception(f"Failed to create service {service_name.value}: {exc}")
            raise

    def _resolve_service_type_from_annotation(self, annotation) -> ServiceType | None:
        """从类型注解解析 ServiceType。"""
        # 注意：处理字符串前向引用。
        annotation_name = annotation if isinstance(annotation, str) else getattr(annotation, "__name__", None)

        if not annotation_name:
            return None

        # 实现：匹配类名到 ServiceType。
        for service_type in ServiceType:
            # 实现：优先匹配已注册类名。
            if service_type in self.service_classes:
                registered_class = self.service_classes[service_type]
                if registered_class.__name__ == annotation_name:
                    return service_type

            # 实现：按命名规则推断 ServiceType。
            expected_name = annotation_name.replace("Service", "").lower() + "_service"
            if service_type.value == expected_name:
                return service_type

        return None

    def _create_service_from_factory(self, service_name: ServiceType, default: ServiceFactory | None = None) -> None:
        """通过工厂创建服务实例（旧系统）。"""
        self._validate_service_creation(service_name, default)

        if service_name == ServiceType.SETTINGS_SERVICE:
            from lfx.services.settings.factory import SettingsServiceFactory

            factory = SettingsServiceFactory()
            if factory not in self.factories:
                self.register_factory(factory)
        else:
            factory = self.factories.get(service_name)

        # 实现：先创建依赖服务。
        if factory is None and default is not None:
            self.register_factory(default)
            factory = default
        if factory is None:
            msg = f"No factory registered for {service_name}"
            raise NoFactoryRegisteredError(msg)
        for dependency in factory.dependencies:
            if dependency not in self.services:
                self._create_service(dependency)

        # 实现：收集依赖注入参数。
        dependent_services = {dep.value: self.services[dep] for dep in factory.dependencies}

        # 实现：创建服务并标记就绪。
        self.services[service_name] = self.factories[service_name].create(**dependent_services)
        self.services[service_name].set_ready()

    def _validate_service_creation(self, service_name: ServiceType, default: ServiceFactory | None = None) -> None:
        """校验服务是否可创建。"""
        if service_name == ServiceType.SETTINGS_SERVICE:
            return
        if service_name not in self.factories and default is None:
            msg = f"No factory registered for the service class '{service_name.name}'"
            raise NoFactoryRegisteredError(msg)

    def update(self, service_name: ServiceType) -> None:
        """重建指定服务实例。"""
        if service_name in self.services:
            logger.debug(f"Update service {service_name}")
            self.services.pop(service_name, None)
            self.get(service_name)

    async def teardown(self) -> None:
        """销毁所有已创建服务。"""
        for service in list(self.services.values()):
            if service is None:
                continue
            logger.debug(f"Teardown service {service.name}")
            try:
                teardown_result = service.teardown()
                if asyncio.iscoroutine(teardown_result):
                    await teardown_result
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"Error in teardown of {service.name}", exc_info=exc)
        self.services = {}
        self.factories = {}

    @classmethod
    def get_factories(cls) -> list[ServiceFactory]:
        """自动发现并返回所有服务工厂。"""
        from lfx.services.factory import ServiceFactory
        from lfx.services.schema import ServiceType

        service_names = [ServiceType(service_type).value.replace("_service", "") for service_type in ServiceType]
        base_module = "lfx.services"
        factories = []

        for name in service_names:
            try:
                module_name = f"{base_module}.{name}.factory"
                module = importlib.import_module(module_name)

                # 实现：查找模块内的工厂子类。
                for _, obj in inspect.getmembers(module, inspect.isclass):
                    if isinstance(obj, type) and issubclass(obj, ServiceFactory) and obj is not ServiceFactory:
                        factories.append(obj())
                        break

            except Exception:  # noqa: BLE001, S110
                # 注意：初始发现阶段允许失败，避免启动噪声。
                pass

        return factories

    def discover_plugins(self, config_dir: Path | None = None) -> None:
        """发现并注册服务插件。

        发现顺序（后者覆盖前者）：
        1) entry points
        2) 配置文件（lfx.toml / pyproject.toml）
        3) 装饰器注册
        """
        if self._plugins_discovered:
            logger.debug("Plugins already discovered, skipping...")
            return

        # 实现：从 settings 获取配置目录。
        if config_dir is None and ServiceType.SETTINGS_SERVICE in self.services:
            settings_service = self.services[ServiceType.SETTINGS_SERVICE]
            if hasattr(settings_service, "settings") and settings_service.settings.config_dir:
                config_dir = Path(settings_service.settings.config_dir)

        logger.debug(f"Starting plugin discovery (config_dir: {config_dir or 'cwd'})...")

        # 实现：从 entry points 发现服务。
        self._discover_from_entry_points()

        # 实现：从配置文件发现服务。
        self._discover_from_config(config_dir)

        self._plugins_discovered = True
        logger.debug(f"Plugin discovery complete. Registered services: {list(self.service_classes.keys())}")

    def _discover_from_entry_points(self) -> None:
        """从 Python entry points 发现服务。"""
        from importlib.metadata import entry_points

        eps = entry_points(group="lfx.services")

        for ep in eps:
            try:
                service_class = ep.load()
                # 注意：entry point 名称需匹配 ServiceType 枚举值。
                service_type = ServiceType(ep.name)
                self.register_service_class(service_type, service_class, override=False)
                logger.debug(f"Loaded service from entry point: {ep.name}")
            except (ValueError, AttributeError) as exc:
                logger.warning(f"Failed to load entry point {ep.name}: {exc}")
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"Error loading entry point {ep.name}: {exc}")

    def _discover_from_config(self, config_dir: Path | None = None) -> None:
        """从配置文件发现服务。"""
        config_dir = Path.cwd() if config_dir is None else Path(config_dir)

        # 注意：优先读取 lfx.toml。
        lfx_config = config_dir / "lfx.toml"
        if lfx_config.exists():
            self._load_config_file(lfx_config)
            return

        # 注意：其次读取 pyproject.toml 的 [tool.lfx.services]。
        pyproject_config = config_dir / "pyproject.toml"
        if pyproject_config.exists():
            self._load_pyproject_config(pyproject_config)

    def _load_config_file(self, config_path: Path) -> None:
        """从 lfx.toml 读取服务配置。"""
        try:
            import tomllib as tomli  # 注意：Python 3.11+ 内置
        except ImportError:
            import tomli  # 注意：Python 3.10 需外部依赖

        try:
            with config_path.open("rb") as f:
                config = tomli.load(f)

            services = config.get("services", {})
            for service_key, service_path in services.items():
                self._register_service_from_path(service_key, service_path)

            logger.debug(f"Loaded {len(services)} services from {config_path}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Failed to load config from {config_path}: {exc}")

    def _load_pyproject_config(self, config_path: Path) -> None:
        """从 pyproject.toml 读取服务配置。"""
        try:
            import tomllib as tomli  # 注意：Python 3.11+ 内置
        except ImportError:
            import tomli  # 注意：Python 3.10 需外部依赖

        try:
            with config_path.open("rb") as f:
                config = tomli.load(f)

            services = config.get("tool", {}).get("lfx", {}).get("services", {})
            for service_key, service_path in services.items():
                self._register_service_from_path(service_key, service_path)

            if services:
                logger.debug(f"Loaded {len(services)} services from {config_path}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Failed to load config from {config_path}: {exc}")

    def _register_service_from_path(self, service_key: str, service_path: str) -> None:
        """通过 `module:class` 路径注册服务类。"""
        try:
            # 注意：service_key 必须匹配 ServiceType。
            service_type = ServiceType(service_key)
        except ValueError:
            logger.warning(f"Invalid service key '{service_key}' - must match ServiceType enum value")
            return

        try:
            # 实现：解析 module:class 格式。
            if ":" not in service_path:
                logger.warning(f"Invalid service path '{service_path}' - must be 'module:class' format")
                return

            module_path, class_name = service_path.split(":", 1)
            module = importlib.import_module(module_path)
            service_class = getattr(module, class_name)

            self.register_service_class(service_type, service_class, override=True)
            logger.debug(f"Registered service from config: {service_key} -> {service_path}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Failed to register service {service_key} from {service_path}: {exc}")


# 注意：懒加载单例服务管理器。
_service_manager: ServiceManager | None = None
_service_manager_lock = threading.Lock()


def get_service_manager() -> ServiceManager:
    """获取服务管理器单例（线程安全懒加载）。"""
    global _service_manager  # noqa: PLW0603
    if _service_manager is None:
        with _service_manager_lock:
            if _service_manager is None:
                _service_manager = ServiceManager()
    return _service_manager
