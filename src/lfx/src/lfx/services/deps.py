"""
模块名称：服务依赖注入与会话管理

本模块提供获取服务实例的便捷函数与数据库会话上下文管理器。
主要功能包括：
- 通过 ServiceType 获取服务实例
- 提供数据库读写会话上下文
- 兼容无数据库环境的 Noop 服务

设计背景：统一服务访问入口，减少调用方耦合。
注意事项：`session_scope` 会自动提交或回滚事务。
"""

from __future__ import annotations

from contextlib import asynccontextmanager, suppress
from http import HTTPStatus
from typing import TYPE_CHECKING

from fastapi import HTTPException
from sqlalchemy.exc import InvalidRequestError

from lfx.log.logger import logger
from lfx.services.schema import ServiceType

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession

    from lfx.services.interfaces import (
        CacheServiceProtocol,
        ChatServiceProtocol,
        DatabaseServiceProtocol,
        SettingsServiceProtocol,
        StorageServiceProtocol,
        TracingServiceProtocol,
        TransactionServiceProtocol,
        VariableServiceProtocol,
    )


def get_service(service_type: ServiceType, default=None):
    """获取指定类型的服务实例。

    契约：若未注册则返回 `default` 创建的实例或 None。
    失败语义：获取过程异常时返回 None。
    """
    from lfx.services.manager import get_service_manager

    service_manager = get_service_manager()

    if not service_manager.are_factories_registered():
        # 注意：确保服务工厂已注册，避免延迟初始化导致空返回。

        service_manager.register_factories(service_manager.get_factories())

    if ServiceType.SETTINGS_SERVICE not in service_manager.factories:
        from lfx.services.settings.factory import SettingsServiceFactory

        service_manager.register_factory(service_factory=SettingsServiceFactory())

    try:
        return service_manager.get(service_type, default)
    except Exception:  # noqa: BLE001
        return None


def get_db_service() -> DatabaseServiceProtocol:
    """获取数据库服务实例。

    契约：若无真实服务则返回 `NoopDatabaseService` 以保证可用性。
    """
    from lfx.services.database.service import NoopDatabaseService
    from lfx.services.schema import ServiceType

    db_service = get_service(ServiceType.DATABASE_SERVICE)
    if db_service is None:
        # 注意：无数据库服务时返回 Noop 实现以保证可用性。
        return NoopDatabaseService()
    return db_service


def get_storage_service() -> StorageServiceProtocol | None:
    """获取存储服务实例。"""
    from lfx.services.schema import ServiceType

    return get_service(ServiceType.STORAGE_SERVICE)


def get_settings_service() -> SettingsServiceProtocol | None:
    """获取设置服务实例。"""
    from lfx.services.schema import ServiceType

    return get_service(ServiceType.SETTINGS_SERVICE)


def get_variable_service() -> VariableServiceProtocol | None:
    """获取变量服务实例。"""
    from lfx.services.schema import ServiceType

    return get_service(ServiceType.VARIABLE_SERVICE)


def get_shared_component_cache_service() -> CacheServiceProtocol | None:
    """获取共享组件缓存服务实例。"""
    from lfx.services.shared_component_cache.factory import SharedComponentCacheServiceFactory

    return get_service(ServiceType.SHARED_COMPONENT_CACHE_SERVICE, SharedComponentCacheServiceFactory())


def get_chat_service() -> ChatServiceProtocol | None:
    """获取聊天服务实例。"""
    from lfx.services.schema import ServiceType

    return get_service(ServiceType.CHAT_SERVICE)


def get_tracing_service() -> TracingServiceProtocol | None:
    """获取链路追踪服务实例。"""
    from lfx.services.schema import ServiceType

    return get_service(ServiceType.TRACING_SERVICE)


def get_transaction_service() -> TransactionServiceProtocol | None:
    """获取事务日志服务实例。"""
    from lfx.services.schema import ServiceType

    return get_service(ServiceType.TRANSACTION_SERVICE)


async def get_session():
    """弃用接口，改用 `session_scope`。"""
    msg = "get_session is deprecated, use session_scope instead"
    logger.warning(msg)
    raise NotImplementedError(msg)


async def injectable_session_scope():
    async with session_scope() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession, None]:
    """异步写会话上下文管理器（自动提交/回滚）。

    关键路径（三步）：
    1) 获取数据库服务并创建会话
    2) 执行业务逻辑并自动提交
    3) 异常时回滚并抛出
    """
    db_service = get_db_service()
    async with db_service._with_session() as session:  # noqa: SLF001
        try:
            yield session
            await session.commit()
        except Exception as e:
            # 注意：根据异常类型选择日志级别。
            if isinstance(e, HTTPException):
                if HTTPStatus.BAD_REQUEST.value <= e.status_code < HTTPStatus.INTERNAL_SERVER_ERROR.value:
                    # 注意：客户端错误（4xx）记录为 info。
                    await logger.ainfo(f"Client error during session scope: {e.status_code}: {e.detail}")
                else:
                    # 注意：服务端错误（5xx）记录为 error。
                    await logger.aexception("An error occurred during the session scope.", exception=e)
            else:
                # 注意：非 HTTP 异常记录为 error。
                await logger.aexception("An error occurred during the session scope.", exception=e)

            # 注意：仅在会话可用时回滚。
            if session.is_active:
                with suppress(InvalidRequestError):
                    # 注意：SQLAlchemy 可能已回滚。
                    await session.rollback()
            raise
        # 注意：会话关闭由 `_with_session()` 管理。


async def injectable_session_scope_readonly():
    async with session_scope_readonly() as session:
        yield session


@asynccontextmanager
async def session_scope_readonly() -> AsyncGenerator[AsyncSession, None]:
    """只读会话上下文管理器（不提交/回滚）。"""
    db_service = get_db_service()
    async with db_service._with_session() as session:  # noqa: SLF001
        yield session
        # 注意：只读会话不提交，关闭由 `_with_session()` 管理。
