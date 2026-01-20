"""
模块名称：服务工具函数

本模块提供各种服务相关的工具函数，包括超级用户管理、服务初始化和清理等功能。
主要功能包括：
- 超级用户创建和管理
- 服务初始化和清理
- 数据库事务和顶点构建清理
- 服务工厂注册

关键组件：
- `setup_superuser`：设置超级用户
- `teardown_superuser`：清理超级用户
- `initialize_services`：初始化所有服务
- `clean_transactions`：清理事务
- `clean_vertex_builds`：清理顶点构建

设计背景：提供服务管理的通用工具函数，简化服务配置和管理过程。
注意事项：这些工具函数在应用启动和关闭时被调用。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from lfx.log.logger import logger
from lfx.services.settings.constants import DEFAULT_SUPERUSER, DEFAULT_SUPERUSER_PASSWORD
from sqlalchemy import delete
from sqlalchemy import exc as sqlalchemy_exc
from sqlmodel import col, select

from langflow.services.auth.utils import create_super_user, verify_password
from langflow.services.cache.base import ExternalAsyncBaseCacheService
from langflow.services.cache.factory import CacheServiceFactory
from langflow.services.database.models.transactions.model import TransactionTable
from langflow.services.database.models.vertex_builds.model import VertexBuildTable
from langflow.services.database.utils import initialize_database
from langflow.services.schema import ServiceType

from .deps import get_db_service, get_service, get_settings_service, session_scope

if TYPE_CHECKING:
    from lfx.services.settings.manager import SettingsService
    from sqlmodel.ext.asyncio.session import AsyncSession


async def get_or_create_super_user(session: AsyncSession, username, password, is_default):
    """获取或创建超级用户。

    契约：检查用户是否存在，如果不存在则创建。
    副作用：可能在数据库中创建新用户。
    失败语义：如果用户存在但凭据不正确则抛出 ValueError。
    
    决策：区分默认和自定义超级用户
    问题：需要处理默认超级用户和自定义超级用户的不同场景
    方案：通过 is_default 参数区分处理逻辑
    代价：增加了逻辑复杂性
    重评：如果超级用户管理逻辑变得更复杂则考虑重构
    """
    from langflow.services.database.models.user.model import User

    stmt = select(User).where(User.username == username)
    result = await session.exec(stmt)
    user = result.first()

    if user and user.is_superuser:
        return None  # Superuser already exists

    if user and is_default:
        if user.is_superuser:
            if verify_password(password, user.password):
                return None
            # Superuser exists but password is incorrect
            # which means that the user has changed the
            # base superuser credentials.
            # This means that the user has already created
            # a superuser and changed the password in the UI
            # so we don't need to do anything.
            await logger.adebug(
                "Superuser exists but password is incorrect. "
                "This means that the user has changed the "
                "base superuser credentials."
            )
            return None
        logger.debug("User with superuser credentials exists but is not a superuser.")
        return None

    if user:
        if verify_password(password, user.password):
            msg = "User with superuser credentials exists but is not a superuser."
            raise ValueError(msg)
        msg = "Incorrect superuser credentials"
        raise ValueError(msg)

    if is_default:
        logger.debug("Creating default superuser.")
    else:
        logger.debug("Creating superuser.")
    return await create_super_user(username, password, db=session)


async def setup_superuser(settings_service: SettingsService, session: AsyncSession) -> None:
    """设置超级用户。

    契约：根据设置创建或更新超级用户。
    副作用：可能在数据库中创建或更新用户。
    失败语义：如果创建失败则抛出异常。
    """
    if settings_service.auth_settings.AUTO_LOGIN:
        await logger.adebug("AUTO_LOGIN is set to True. Creating default superuser.")
        username = DEFAULT_SUPERUSER
        password = DEFAULT_SUPERUSER_PASSWORD.get_secret_value()
    else:
        # Remove the default superuser if it exists
        await teardown_superuser(settings_service, session)
        # If AUTO_LOGIN is disabled, attempt to use configured credentials
        # or fall back to default credentials if none are provided.
        username = settings_service.auth_settings.SUPERUSER or DEFAULT_SUPERUSER
        password = (settings_service.auth_settings.SUPERUSER_PASSWORD or DEFAULT_SUPERUSER_PASSWORD).get_secret_value()

    if not username or not password:
        msg = "Username and password must be set"
        raise ValueError(msg)

    is_default = (username == DEFAULT_SUPERUSER) and (password == DEFAULT_SUPERUSER_PASSWORD.get_secret_value())

    try:
        user = await get_or_create_super_user(
            session=session, username=username, password=password, is_default=is_default
        )
        if user is not None:
            await logger.adebug("Superuser created successfully.")
    except Exception as exc:
        logger.exception(exc)
        msg = "Could not create superuser. Please create a superuser manually."
        raise RuntimeError(msg) from exc
    finally:
        # Scrub credentials from in-memory settings after setup
        settings_service.auth_settings.reset_credentials()


async def teardown_superuser(settings_service, session: AsyncSession) -> None:
    """清理超级用户。

    契约：根据设置清理默认超级用户。
    副作用：可能从数据库中删除用户。
    失败语义：如果删除失败则抛出异常。
    """
    # If AUTO_LOGIN is True, we will remove the default superuser
    # from the database.

    if not settings_service.auth_settings.AUTO_LOGIN:
        try:
            await logger.adebug("AUTO_LOGIN is set to False. Removing default superuser if exists.")
            username = DEFAULT_SUPERUSER
            from langflow.services.database.models.user.model import User

            stmt = select(User).where(User.username == username)
            result = await session.exec(stmt)
            user = result.first()
            # Check if super was ever logged in, if not delete it
            # if it has logged in, it means the user is using it to login
            if user and user.is_superuser is True and not user.last_login_at:
                await session.delete(user)
                await logger.adebug("Default superuser removed successfully.")

        except Exception as exc:
            logger.exception(exc)
            msg = "Could not remove default superuser."
            raise RuntimeError(msg) from exc


async def teardown_services() -> None:
    """清理所有服务。

    契约：执行所有服务的清理操作。
    副作用：停止和清理所有服务。
    失败语义：如果清理失败则记录错误。
    """
    async with session_scope() as session:
        await teardown_superuser(get_settings_service(), session)

    from lfx.services.manager import get_service_manager

    service_manager = get_service_manager()
    await service_manager.teardown()


def initialize_settings_service() -> None:
    """初始化设置管理器。

    契约：初始化设置服务。
    副作用：注册设置服务工厂。
    失败语义：如果初始化失败则抛出异常。
    """
    from lfx.services.settings import factory as settings_factory

    get_service(ServiceType.SETTINGS_SERVICE, settings_factory.SettingsServiceFactory())


def initialize_session_service() -> None:
    """初始化会话管理器。

    契约：初始化缓存和会话服务。
    副作用：注册缓存和会话服务工厂。
    失败语义：如果初始化失败则抛出异常。
    """
    from langflow.services.cache import factory as cache_factory
    from langflow.services.session import factory as session_service_factory

    initialize_settings_service()

    get_service(
        ServiceType.CACHE_SERVICE,
        cache_factory.CacheServiceFactory(),
    )

    get_service(
        ServiceType.SESSION_SERVICE,
        session_service_factory.SessionServiceFactory(),
    )


async def clean_transactions(settings_service: SettingsService, session: AsyncSession) -> None:
    """清理数据库中的旧事务。

    此函数删除超出最大保留数量的事务（在设置中配置）。
    它按时间戳降序排列事务并删除超出限制的最旧事务。

    Args:
        settings_service: 包含配置的设置服务，如 max_transactions_to_keep
        session: 用于删除操作的数据库会话
    
    契约：删除超出限制的旧事务。
    副作用：从数据库中删除事务记录。
    失败语义：如果清理失败则记录错误但不抛出异常。
    """
    try:
        # Delete transactions using bulk delete
        delete_stmt = delete(TransactionTable).where(
            col(TransactionTable.id).in_(
                select(TransactionTable.id)
                .order_by(col(TransactionTable.timestamp).desc())
                .offset(settings_service.settings.max_transactions_to_keep)
            )
        )

        await session.exec(delete_stmt)
        logger.debug("Successfully cleaned up old transactions")
    except (sqlalchemy_exc.SQLAlchemyError, asyncio.TimeoutError) as exc:
        logger.error(f"Error cleaning up transactions: {exc!s}")
        # Don't re-raise since this is a cleanup task


async def clean_vertex_builds(settings_service: SettingsService, session: AsyncSession) -> None:
    """清理数据库中的旧顶点构建。

    此函数删除超出最大保留数量的顶点构建（在设置中配置）。
    它按时间戳降序排列顶点构建并删除超出限制的最旧构建。

    Args:
        settings_service: 包含配置的设置服务，如 max_vertex_builds_to_keep
        session: 用于删除操作的数据库会话
    
    契约：删除超出限制的旧顶点构建。
    副作用：从数据库中删除顶点构建记录。
    失败语义：如果清理失败则记录错误但不抛出异常。
    """
    try:
        # Delete vertex builds using bulk delete
        delete_stmt = delete(VertexBuildTable).where(
            col(VertexBuildTable.id).in_(
                select(VertexBuildTable.id)
                .order_by(col(VertexBuildTable.timestamp).desc())
                .offset(settings_service.settings.max_vertex_builds_to_keep)
            )
        )

        await session.exec(delete_stmt)
        logger.debug("Successfully cleaned up old vertex builds")
    except (sqlalchemy_exc.SQLAlchemyError, asyncio.TimeoutError) as exc:
        logger.error(f"Error cleaning up vertex builds: {exc!s}")
        # Don't re-raise since this is a cleanup task


def register_all_service_factories() -> None:
    """向服务管理器注册所有可用的服务工厂。

    契约：注册所有服务工厂。
    副作用：向服务管理器添加工厂实例。
    失败语义：如果注册失败则抛出异常。
    
    决策：在一个函数中注册所有服务
    问题：需要确保所有服务都被正确注册
    方案：集中注册所有服务工厂
    代价：函数变得较长
    重评：如果服务过多则考虑分批注册
    """
    # Import all service factories
    from lfx.services.manager import get_service_manager

    service_manager = get_service_manager()
    from lfx.services.mcp_composer import factory as mcp_composer_factory
    from lfx.services.settings import factory as settings_factory

    from langflow.services.auth import factory as auth_factory
    from langflow.services.cache import factory as cache_factory
    from langflow.services.chat import factory as chat_factory
    from langflow.services.database import factory as database_factory
    from langflow.services.job_queue import factory as job_queue_factory
    from langflow.services.session import factory as session_factory
    from langflow.services.shared_component_cache import factory as shared_component_cache_factory
    from langflow.services.state import factory as state_factory
    from langflow.services.storage import factory as storage_factory
    from langflow.services.store import factory as store_factory
    from langflow.services.task import factory as task_factory
    from langflow.services.telemetry import factory as telemetry_factory
    from langflow.services.tracing import factory as tracing_factory
    from langflow.services.transaction import factory as transaction_factory
    from langflow.services.variable import factory as variable_factory

    # Register all factories
    service_manager.register_factory(settings_factory.SettingsServiceFactory())
    service_manager.register_factory(cache_factory.CacheServiceFactory())
    service_manager.register_factory(chat_factory.ChatServiceFactory())
    service_manager.register_factory(database_factory.DatabaseServiceFactory())
    service_manager.register_factory(session_factory.SessionServiceFactory())
    service_manager.register_factory(storage_factory.StorageServiceFactory())
    service_manager.register_factory(variable_factory.VariableServiceFactory())
    service_manager.register_factory(telemetry_factory.TelemetryServiceFactory())
    service_manager.register_factory(tracing_factory.TracingServiceFactory())
    service_manager.register_factory(transaction_factory.TransactionServiceFactory())
    service_manager.register_factory(state_factory.StateServiceFactory())
    service_manager.register_factory(job_queue_factory.JobQueueServiceFactory())
    service_manager.register_factory(task_factory.TaskServiceFactory())
    service_manager.register_factory(store_factory.StoreServiceFactory())
    service_manager.register_factory(shared_component_cache_factory.SharedComponentCacheServiceFactory())
    service_manager.register_factory(auth_factory.AuthServiceFactory())
    service_manager.register_factory(mcp_composer_factory.MCPComposerServiceFactory())
    service_manager.set_factory_registered()


async def initialize_services(*, fix_migration: bool = False) -> None:
    """初始化所有所需的服务。

    契约：初始化并配置所有必需的服务。
    副作用：启动数据库、创建超级用户、清理旧数据。
    失败语义：如果初始化失败则抛出异常。
    
    决策：按顺序初始化服务
    问题：服务之间存在依赖关系
    方案：按照依赖顺序初始化服务
    代价：初始化过程较长
    重评：如果初始化时间过长则考虑并行初始化
    """
    # Register all service factories first
    register_all_service_factories()

    cache_service = get_service(ServiceType.CACHE_SERVICE, default=CacheServiceFactory())
    # Test external cache connection
    if isinstance(cache_service, ExternalAsyncBaseCacheService) and not (await cache_service.is_connected()):
        msg = "Cache service failed to connect to external database"
        raise ConnectionError(msg)

    # Setup the superuser
    await initialize_database(fix_migration=fix_migration)
    db_service = get_db_service()
    await db_service.initialize_alembic_log_file()
    async with session_scope() as session:
        settings_service = get_service(ServiceType.SETTINGS_SERVICE)
        await setup_superuser(settings_service, session)
    try:
        await get_db_service().assign_orphaned_flows_to_superuser()
    except sqlalchemy_exc.IntegrityError as exc:
        await logger.awarning(f"Error assigning orphaned flows to the superuser: {exc!s}")

    async with session_scope() as session:
        await clean_transactions(settings_service, session)
        await clean_vertex_builds(settings_service, session)