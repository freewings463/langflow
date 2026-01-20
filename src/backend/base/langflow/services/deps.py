"""
模块名称：服务依赖注入工具

本模块提供服务依赖注入和获取的功能，包括各种服务的便捷访问方法。
主要功能包括：
- 服务获取和依赖注入
- 各种特定服务的便捷访问方法
- 会话管理上下文

关键组件：
- `get_service`：通用服务获取方法
- `get_*_service`：特定服务获取方法
- `session_scope`：会话范围上下文管理器

设计背景：提供统一的服务获取接口，简化依赖注入过程。
注意事项：这些方法是服务访问的主要入口点。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Union

from langflow.services.schema import ServiceType

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlmodel.ext.asyncio.session import AsyncSession

    from langflow.services.cache.service import AsyncBaseCacheService, CacheService
    from langflow.services.chat.service import ChatService
    from langflow.services.database.service import DatabaseService
    from langflow.services.session.service import SessionService
    from langflow.services.state.service import StateService
    from langflow.services.store.service import StoreService
    from langflow.services.task.service import TaskService
    from langflow.services.tracing.service import TracingService
    from langflow.services.variable.service import VariableService

# These imports MUST be outside TYPE_CHECKING because FastAPI uses eval_str=True
# to evaluate type annotations, and these types are used as return types for
# dependency functions that FastAPI evaluates at module load time.
from lfx.services.settings.service import SettingsService  # noqa: TC002

from langflow.services.job_queue.service import JobQueueService  # noqa: TC001
from langflow.services.storage.service import StorageService  # noqa: TC001
from langflow.services.telemetry.service import TelemetryService  # noqa: TC001


def get_service(service_type: ServiceType, default=None):
    """获取指定类型的服务实例。

    契约：返回给定服务类型的实例。
    副作用：可能初始化服务管理器。
    失败语义：如果找不到服务且没有默认值，则抛出异常。
    
    决策：使用服务管理器模式
    问题：需要统一的服务访问方式
    方案：通过服务类型获取对应服务实例
    代价：增加一层间接调用
    重评：当需要更灵活的服务发现机制时
    """
    from lfx.services.manager import get_service_manager

    service_manager = get_service_manager()

    if not service_manager.are_factories_registered():
        # ! This is a workaround to ensure that the service manager is initialized
        # ! Not optimal, but it works for now
        from lfx.services.manager import ServiceManager

        service_manager.register_factories(ServiceManager.get_factories())
    return service_manager.get(service_type, default)


def get_telemetry_service() -> TelemetryService:
    """获取 TelemetryService 实例。

    契约：返回 TelemetryService 实例。
    副作用：可能初始化服务。
    失败语义：如果服务不可用则抛出异常。
    """
    from langflow.services.telemetry.factory import TelemetryServiceFactory

    return get_service(ServiceType.TELEMETRY_SERVICE, TelemetryServiceFactory())


def get_tracing_service() -> TracingService:
    """获取 TracingService 实例。

    契约：返回 TracingService 实例。
    副作用：可能初始化服务。
    失败语义：如果服务不可用则抛出异常。
    """
    from langflow.services.tracing.factory import TracingServiceFactory

    return get_service(ServiceType.TRACING_SERVICE, TracingServiceFactory())


def get_state_service() -> StateService:
    """获取 StateService 实例。

    契约：返回 StateService 实例。
    副作用：可能初始化服务。
    失败语义：如果服务不可用则抛出异常。
    """
    from langflow.services.state.factory import StateServiceFactory

    return get_service(ServiceType.STATE_SERVICE, StateServiceFactory())


def get_storage_service() -> StorageService:
    """获取存储服务实例。

    契约：返回存储服务实例。
    副作用：可能初始化服务。
    失败语义：如果服务不可用则抛出异常。
    """
    from langflow.services.storage.factory import StorageServiceFactory

    return get_service(ServiceType.STORAGE_SERVICE, default=StorageServiceFactory())


def get_variable_service() -> VariableService:
    """获取 VariableService 实例。

    契约：返回 VariableService 实例。
    副作用：可能初始化服务。
    失败语义：如果服务不可用则抛出异常。
    """
    from langflow.services.variable.factory import VariableServiceFactory

    return get_service(ServiceType.VARIABLE_SERVICE, VariableServiceFactory())


def is_settings_service_initialized() -> bool:
    """检查 SettingsService 是否已初始化而不触发初始化。

    契约：返回 SettingsService 是否已初始化的状态。
    副作用：无。
    失败语义：不抛出异常。
    """
    from lfx.services.manager import get_service_manager

    return ServiceType.SETTINGS_SERVICE in get_service_manager().services


def get_settings_service() -> SettingsService:
    """获取 SettingsService 实例。

    如果服务尚未初始化，则在返回之前初始化。
    
    契约：返回 SettingsService 实例。
    副作用：可能初始化服务。
    失败语义：如果服务无法获取或初始化则抛出异常。
    """
    from lfx.services.settings.factory import SettingsServiceFactory

    return get_service(ServiceType.SETTINGS_SERVICE, SettingsServiceFactory())


def get_db_service() -> DatabaseService:
    """获取 DatabaseService 实例。

    契约：返回 DatabaseService 实例。
    副作用：可能初始化服务。
    失败语义：如果服务不可用则抛出异常。
    """
    from langflow.services.database.factory import DatabaseServiceFactory

    return get_service(ServiceType.DATABASE_SERVICE, DatabaseServiceFactory())


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    msg = "get_session is deprecated, use session_scope instead"
    raise NotImplementedError(msg)


@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession, None]:
    """异步会话范围的上下文管理器。

    此上下文管理器用于管理数据库操作的异步会话范围。
    它确保在没有异常发生时会话被正确提交，
    在发生异常时进行回滚。

    契约：提供异步会话上下文。
    副作用：创建和管理数据库会话。
    失败语义：如果会话操作期间发生错误则抛出异常。
    """
    from lfx.services.deps import session_scope as lfx_session_scope

    async with lfx_session_scope() as session:
        yield session


def get_cache_service() -> Union[CacheService, AsyncBaseCacheService]:  # noqa: UP007
    """获取缓存服务实例。

    契约：返回缓存服务实例。
    副作用：可能初始化服务。
    失败语义：如果服务不可用则抛出异常。
    """
    from langflow.services.cache.factory import CacheServiceFactory

    return get_service(ServiceType.CACHE_SERVICE, CacheServiceFactory())


def get_shared_component_cache_service() -> CacheService:
    """获取缓存服务实例。

    契约：返回缓存服务实例。
    副作用：可能初始化服务。
    失败语义：如果服务不可用则抛出异常。
    """
    from langflow.services.shared_component_cache.factory import SharedComponentCacheServiceFactory

    return get_service(ServiceType.SHARED_COMPONENT_CACHE_SERVICE, SharedComponentCacheServiceFactory())


def get_session_service() -> SessionService:
    """获取会话服务实例。

    契约：返回会话服务实例。
    副作用：可能初始化服务。
    失败语义：如果服务不可用则抛出异常。
    """
    from langflow.services.session.factory import SessionServiceFactory

    return get_service(ServiceType.SESSION_SERVICE, SessionServiceFactory())


def get_task_service() -> TaskService:
    """获取 TaskService 实例。

    契约：返回 TaskService 实例。
    副作用：可能初始化服务。
    失败语义：如果服务不可用则抛出异常。
    """
    from langflow.services.task.factory import TaskServiceFactory

    return get_service(ServiceType.TASK_SERVICE, TaskServiceFactory())


def get_chat_service() -> ChatService:
    """获取聊天服务实例。

    契约：返回聊天服务实例。
    副作用：可能初始化服务。
    失败语义：如果服务不可用则抛出异常。
    """
    return get_service(ServiceType.CHAT_SERVICE)


def get_store_service() -> StoreService:
    """获取 StoreService 实例。

    契约：返回 StoreService 实例。
    副作用：可能初始化服务。
    失败语义：如果服务不可用则抛出异常。
    """
    return get_service(ServiceType.STORE_SERVICE)


def get_queue_service() -> JobQueueService:
    """获取 QueueService 实例。"""
    from langflow.services.job_queue.factory import JobQueueServiceFactory

    return get_service(ServiceType.JOB_QUEUE_SERVICE, JobQueueServiceFactory())