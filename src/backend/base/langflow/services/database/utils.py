"""
模块名称：数据库初始化与会话工具

本模块提供数据库初始化流程与临时会话获取工具。
主要功能包括：建表、健康检查、迁移执行以及迁移异常修复路径。

关键组件：`initialize_database` / `session_getter` / `Result` / `TableResults`
设计背景：将数据库初始化步骤集中管理，统一日志与异常语义。
使用场景：应用启动或测试环境初始化数据库。
注意事项：迁移失败时可能会删除 `alembic_version` 表并重试。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from alembic.util.exc import CommandError
from lfx.log.logger import logger
from sqlmodel import text
from sqlmodel.ext.asyncio.session import AsyncSession

if TYPE_CHECKING:
    from langflow.services.database.service import DatabaseService


async def initialize_database(*, fix_migration: bool = False) -> None:
    """初始化数据库、检查健康并执行迁移。

    契约：
    - 输入：`fix_migration` 控制是否尝试修复迁移差异。
    - 输出：`None`。
    - 副作用：创建表、执行迁移、可能删除 `alembic_version` 表。
    - 失败语义：建表/健康检查/迁移失败时抛 `RuntimeError` 或透传异常。

    关键路径（三步）：
    1) 创建数据库与表（可选重试）。
    2) 执行 schema 健康检查。
    3) 运行 Alembic 迁移，必要时修复并重试。

    决策：将初始化流程串联在单入口函数内。
    问题：启动时需要统一的建表/迁移/健康检查流程。
    方案：按固定顺序执行并在特定错误时做自修复。
    代价：失败路径可能删除迁移记录，增加恢复成本。
    重评：当迁移体系支持自动修复或手动批准时收敛自修复逻辑。

    排障入口：日志关键字 `Initializing database` / `Wrong revision in DB`。
    """
    await logger.adebug("Initializing database")
    from langflow.services.deps import get_db_service

    database_service: DatabaseService = get_db_service()
    try:
        if database_service.settings_service.settings.database_connection_retry:
            await database_service.create_db_and_tables_with_retry()
        else:
            await database_service.create_db_and_tables()
    except Exception as exc:
        # 注意：重复建表错误可忽略，避免启动失败。
        if "already exists" not in str(exc):
            msg = "Error creating DB and tables"
            await logger.aexception(msg)
            raise RuntimeError(msg) from exc
    try:
        await database_service.check_schema_health()
    except Exception as exc:
        msg = "Error checking schema health"
        logger.exception(msg)
        raise RuntimeError(msg) from exc
    try:
        await database_service.run_migrations(fix=fix_migration)
    except CommandError as exc:
        # 注意：仅处理已知迁移错误，未知错误直接抛出。
        if "overlaps with other requested revisions" not in str(
            exc
        ) and "Can't locate revision identified by" not in str(exc):
            raise
        # 注意：迁移版本不匹配时清理 `alembic_version` 后重试。
        logger.warning("Wrong revision in DB, deleting alembic_version table and running migrations again")
        async with session_getter(database_service) as session:
            await session.exec(text("DROP TABLE alembic_version"))
        await database_service.run_migrations(fix=fix_migration)
    except Exception as exc:
        # 注意：其他异常仅在非“已存在”场景记录并抛出。
        if "already exists" not in str(exc):
            logger.exception(exc)
        raise
    await logger.adebug("Database initialized")


@asynccontextmanager
async def session_getter(db_service: DatabaseService):
    """获取临时会话并在异常时回滚。

    契约：
    - 输入：`db_service`，提供 `engine`。
    - 输出：异步上下文中的 `AsyncSession`。
    - 副作用：异常时执行 `rollback`，最终关闭会话。
    - 失败语义：异常透传，并保证回滚与关闭。

    关键路径（三步）：
    1) 基于 `engine` 创建 `AsyncSession`。
    2) 向调用方 `yield` 会话。
    3) 异常回滚并在 `finally` 关闭。

    决策：统一在上下文管理器内处理回滚与关闭。
    问题：调用方可能忽略异常清理，导致连接泄漏。
    方案：在 `asynccontextmanager` 中集中处理。
    代价：会话生命周期被固定在上下文中。
    重评：当需要外部统一事务管理时改为由调用方控制关闭。
    """
    try:
        session = AsyncSession(db_service.engine, expire_on_commit=False)
        yield session
    except Exception:
        await logger.aexception("Session rollback because of exception")
        await session.rollback()
        raise
    finally:
        await session.close()


@dataclass
class Result:
    """单项校验结果载体。

    契约：
    - 字段：`name`/`type`/`success`。
    - 用途：描述表或列的健康检查结果。
    - 失败语义：不抛异常，仅承载状态。
    """
    name: str
    type: str
    success: bool


@dataclass
class TableResults:
    """表级校验结果集合。

    契约：
    - 字段：`table_name` 与对应 `results` 列表。
    - 用途：汇总单表的多项校验结果。
    - 失败语义：不抛异常，仅承载状态。
    """
    table_name: str
    results: list[Result]
