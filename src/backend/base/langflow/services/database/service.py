"""
模块名称：数据库服务核心实现

本模块提供数据库连接、迁移与健康检查的核心服务实现。
主要功能包括：创建异步引擎、会话工厂、执行 `Alembic` 迁移、校验表结构与清理资源。

关键组件：`DatabaseService`
设计背景：集中管理数据库生命周期，避免在多处散落引擎与迁移逻辑。
使用场景：应用启动初始化数据库、运行期重载连接、测试健康检查。
注意事项：`SQLite` 会设置 `PRAGMA`，迁移失败可能触发自动回滚/重试。
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
import sys
import time
from contextlib import asynccontextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
import sqlalchemy as sa
from alembic import command, util
from alembic.config import Config
from lfx.log.logger import logger
from lfx.services.deps import session_scope
from sqlalchemy import event, inspect
from sqlalchemy.dialects import sqlite as dialect_sqlite
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select, text
from sqlmodel.ext.asyncio.session import AsyncSession as SQLModelAsyncSession
from tenacity import retry, stop_after_attempt, wait_fixed

from langflow.initial_setup.constants import STARTER_FOLDER_NAME
from langflow.services.base import Service
from langflow.services.database import models
from langflow.services.database.models.user.crud import get_user_by_username
from langflow.services.database.session import NoopSession
from langflow.services.database.utils import Result, TableResults
from langflow.services.deps import get_settings_service
from langflow.services.utils import teardown_superuser

if TYPE_CHECKING:
    from lfx.services.settings.service import SettingsService


class DatabaseService(Service):
    """数据库服务核心对象。

    契约：
    - 输入：构造需要 `settings_service`，包含 `database_url` 与连接配置。
    - 输出：提供引擎、会话与迁移等数据库能力。
    - 副作用：注册事件监听、创建引擎与会话工厂、读取迁移配置路径。
    - 失败语义：缺失 `database_url` 时抛 `ValueError`。

    关键路径：
    1) 规范化数据库连接地址并建立引擎。
    2) 创建异步会话工厂与 `Alembic` 配置路径。
    3) 提供迁移、建表与健康检查方法。

    决策：以单服务对象集中承载数据库生命周期。
    问题：连接与迁移逻辑分散会导致配置不一致与难以维护。
    方案：统一封装在 `DatabaseService` 内部。
    代价：服务对象职责较重，需要严格测试覆盖。
    重评：当数据库能力拆分为多个微服务时再拆分职责。
    """

    name = "database_service"

    def __init__(self, settings_service: SettingsService):
        """初始化数据库服务并创建引擎与会话工厂。

        契约：
        - 输入：`settings_service`，要求包含 `database_url` 与迁移/连接配置。
        - 输出：初始化后的 `DatabaseService` 实例。
        - 副作用：注册 `SQLite` 连接事件、创建异步引擎与会话工厂。
        - 失败语义：`database_url` 缺失时抛 `ValueError`。

        关键路径（三步）：
        1) 规范化连接地址并设置 `Alembic` 路径。
        2) 注册连接事件并创建引擎（可选重试）。
        3) 构建异步会话工厂与日志配置。

        决策：构造期完成引擎与会话工厂初始化。
        问题：运行期需要立即可用的数据库连接能力。
        方案：在构造函数中创建引擎并设置会话工厂。
        代价：初始化成本前置，启动耗时增加。
        重评：当需要延迟连接或多数据库实例时改为惰性初始化。
        """
        self._logged_pragma = False
        self.settings_service = settings_service
        if settings_service.settings.database_url is None:
            msg = "No database URL provided"
            raise ValueError(msg)
        self.database_url: str = settings_service.settings.database_url
        self._sanitize_database_url()

        # 注意：脚本与 `alembic.ini` 依赖固定目录结构。
        langflow_dir = Path(__file__).parent.parent.parent
        self.script_location = langflow_dir / "alembic"
        self.alembic_cfg_path = langflow_dir / "alembic.ini"

        # 注意：`SQLite` 连接事件需绑定实例方法，避免装饰器丢失 `self`。
        event.listen(Engine, "connect", self.on_connection)
        if self.settings_service.settings.database_connection_retry:
            self.engine = self._create_engine_with_retry()
        else:
            self.engine = self._create_engine()

        # 注意：必须使用 `SQLModelAsyncSession` 才支持 `exec()`。
        self.async_session_maker = async_sessionmaker(
            self.engine,
            class_=SQLModelAsyncSession,  # 注意：使用 `SQLModel` 会话以支持 `exec()`。
            expire_on_commit=False,
        )

        # 注意：根据配置决定 `Alembic` 日志输出路径。
        alembic_log_file = self.settings_service.settings.alembic_log_file
        self.alembic_log_to_stdout = self.settings_service.settings.alembic_log_to_stdout
        if self.alembic_log_to_stdout:
            self.alembic_log_path = None
        elif Path(alembic_log_file).is_absolute():
            self.alembic_log_path = Path(alembic_log_file)
        else:
            self.alembic_log_path = Path(langflow_dir) / alembic_log_file

    async def initialize_alembic_log_file(self):
        """确保 `Alembic` 日志文件可写。

        契约：
        - 输入：无。
        - 输出：`None`。
        - 副作用：创建日志目录与文件。
        - 失败语义：文件系统异常透传。

        关键路径：
        1) 若配置输出到标准输出则直接返回。
        2) 创建目录并触碰日志文件。

        决策：在运行迁移前预创建日志文件。
        问题：日志路径不存在会导致迁移日志写入失败。
        方案：使用 `anyio.Path` 创建目录与文件。
        代价：增加一次文件系统写入。
        重评：当使用集中日志系统时可跳过文件准备。
        """
        if self.alembic_log_to_stdout:
            return
        # 注意：确保日志目录与文件存在，避免迁移期间写入失败。
        await anyio.Path(self.alembic_log_path.parent).mkdir(parents=True, exist_ok=True)
        await anyio.Path(self.alembic_log_path).touch(exist_ok=True)

    def reload_engine(self) -> None:
        """重新加载数据库引擎与会话工厂。

        契约：
        - 输入：无。
        - 输出：`None`。
        - 副作用：重建引擎与会话工厂。
        - 失败语义：连接配置异常时抛异常。

        关键路径：
        1) 重新规范化连接地址。
        2) 依据配置创建引擎（可重试）。
        3) 重建 `async_session_maker`。

        决策：在配置变化时重建引擎而非复用旧连接。
        问题：运行期配置变更会导致连接参数不一致。
        方案：释放并重建引擎/会话工厂。
        代价：旧连接池状态丢失。
        重评：当支持热更新连接池时改为增量更新。
        """
        self._sanitize_database_url()
        if self.settings_service.settings.database_connection_retry:
            self.engine = self._create_engine_with_retry()
        else:
            self.engine = self._create_engine()

        self.async_session_maker = async_sessionmaker(
            self.engine,
            class_=SQLModelAsyncSession,
            expire_on_commit=False,
        )

    def _sanitize_database_url(self):
        """规范化数据库连接地址。

        契约：输入为内部 `database_url` 字符串，输出为规范化后的地址。
        副作用：更新 `self.database_url`。
        失败语义：格式异常时可能抛出索引错误。
        """
        url_components = self.database_url.split("://", maxsplit=1)

        driver = url_components[0]

        if driver == "sqlite":
            driver = "sqlite+aiosqlite"
        elif driver in {"postgresql", "postgres"}:
            if driver == "postgres":
                logger.warning(
                    "The postgres dialect in the database URL is deprecated. "
                    "Use postgresql instead. "
                    "To avoid this warning, update the database URL."
                )
            driver = "postgresql+psycopg"

        self.database_url = f"{driver}://{url_components[1]}"

    def _build_connection_kwargs(self):
        """合并连接参数并兼容旧配置。

        契约：
        - 输入：无（读取 `settings_service.settings`）。
        - 输出：连接参数字典。
        - 副作用：记录弃用配置告警。
        - 失败语义：无显式抛错，异常透传。

        关键路径：
        1) 以 `db_connection_settings` 为基础。
        2) 对显式设置的旧字段进行覆盖。

        决策：旧字段覆盖新配置以保持兼容。
        问题：历史配置项仍被使用，需兼容升级路径。
        方案：检测 `model_fields_set` 并覆盖新配置。
        代价：存在配置歧义，需逐步废弃旧字段。
        重评：当旧字段完全移除后删除覆盖逻辑。
        """
        settings = self.settings_service.settings
        # 注意：以 `db_connection_settings` 为基础合并配置。
        connection_kwargs = settings.db_connection_settings.copy()

        # 注意：显式设置的旧字段优先级更高。
        if "pool_size" in settings.model_fields_set:
            logger.warning("pool_size is deprecated. Use db_connection_settings['pool_size'] instead.")
            connection_kwargs["pool_size"] = settings.pool_size
        if "max_overflow" in settings.model_fields_set:
            logger.warning("max_overflow is deprecated. Use db_connection_settings['max_overflow'] instead.")
            connection_kwargs["max_overflow"] = settings.max_overflow

        return connection_kwargs

    def _create_engine(self) -> AsyncEngine:
        """创建异步数据库引擎。

        契约：
        - 输入：无（读取连接配置）。
        - 输出：`AsyncEngine`。
        - 副作用：无直接 I/O；仅构造引擎对象。
        - 失败语义：配置非法时抛异常。

        关键路径：
        1) 读取并合并连接参数。
        2) 处理 `poolclass` 并校验类型。
        3) 调用 `create_async_engine` 创建引擎。

        决策：在创建阶段校验 `poolclass` 并回退默认。
        问题：错误的连接池类会导致运行期崩溃。
        方案：无效时记录错误并删除该配置。
        代价：用户配置可能被忽略。
        重评：当需要严格失败策略时改为直接抛错。
        """
        # 注意：允许空字典作为显式配置。
        kwargs = self._build_connection_kwargs()

        poolclass_key = kwargs.get("poolclass")
        if poolclass_key is not None:
            pool_class = getattr(sa.pool, poolclass_key, None)
            if pool_class and issubclass(pool_class, sa.pool.Pool):
                logger.debug(f"Using poolclass: {poolclass_key}.")
                kwargs["poolclass"] = pool_class
            else:
                logger.error(f"Invalid poolclass '{poolclass_key}' specified. Using default pool class.")
                kwargs.pop("poolclass", None)

        return create_async_engine(
            self.database_url,
            connect_args=self._get_connect_args(),
            **kwargs,
        )

    @retry(wait=wait_fixed(2), stop=stop_after_attempt(10))
    def _create_engine_with_retry(self) -> AsyncEngine:
        """带重试策略创建异步引擎。

        契约：
        - 输入：无。
        - 输出：`AsyncEngine`。
        - 副作用：触发重试等待。
        - 失败语义：超过重试次数后抛出异常。

        决策：使用 `tenacity` 固定间隔重试。
        问题：数据库启动时可能短暂不可达。
        方案：最多重试 10 次，每次间隔 2 秒。
        代价：启动时间延长。
        重评：当数据库 SLA 提升或改为健康探针时调整重试策略。
        """
        return self._create_engine()

    def _get_connect_args(self):
        """生成驱动连接参数。

        契约：
        - 输入：无（读取 `settings`）。
        - 输出：连接参数字典。
        - 副作用：无。
        - 失败语义：无显式抛错，异常透传。

        关键路径：
        1) 若存在 `db_driver_connection_settings` 直接返回。
        2) `sqlite` 设置 `check_same_thread` 与 `timeout`。
        3) `postgres` 设置时区为 `utc`。

        决策：优先使用显式驱动参数。
        问题：不同数据库驱动需要不同连接参数。
        方案：按 `database_url` 前缀分支设置默认参数。
        代价：新增驱动需补充分支。
        重评：当统一配置下发时改为配置化映射。
        """
        settings = self.settings_service.settings

        if settings.db_driver_connection_settings is not None:
            return settings.db_driver_connection_settings

        if settings.database_url and settings.database_url.startswith("sqlite"):
            return {
                "check_same_thread": False,
                "timeout": settings.db_connect_timeout,
            }
        # 注意：`PostgreSQL` 连接统一设置 `utc` 时区。
        if settings.database_url and settings.database_url.startswith(("postgresql", "postgres")):
            return {"options": "-c timezone=utc"}
        return {}

    def on_connection(self, dbapi_connection, _connection_record) -> None:
        """`SQLite` 连接时注入 `PRAGMA` 参数。

        契约：
        - 输入：`dbapi_connection` 为底层连接对象。
        - 输出：`None`。
        - 副作用：对 `SQLite` 连接执行多条 `PRAGMA`。
        - 失败语义：单条 `PRAGMA` 失败仅记录日志，不中断连接。

        关键路径：
        1) 检测是否为 `SQLite` 连接。
        2) 从配置读取 `sqlite_pragmas` 并构造语句。
        3) 逐条执行并记录失败。

        决策：在连接建立时设置 `PRAGMA`。
        问题：`SQLite` 需在连接级别配置性能与一致性参数。
        方案：注册 `Engine` 的 `connect` 事件执行设置。
        代价：连接建立时增加额外执行开销。
        重评：当迁移到非 `SQLite` 或统一配置时移除该逻辑。
        """
        if isinstance(dbapi_connection, sqlite3.Connection | dialect_sqlite.aiosqlite.AsyncAdapt_aiosqlite_connection):
            pragmas: dict = self.settings_service.settings.sqlite_pragmas or {}
            pragmas_list = []
            for key, val in pragmas.items():
                pragmas_list.append(f"PRAGMA {key} = {val}")
            if not self._logged_pragma:
                logger.debug(f"sqlite connection, setting pragmas: {pragmas_list}")
                self._logged_pragma = True
            if pragmas_list:
                cursor = dbapi_connection.cursor()
                try:
                    for pragma in pragmas_list:
                        try:
                            cursor.execute(pragma)
                        except OperationalError:
                            logger.exception(f"Failed to set PRAGMA {pragma}")
                        except GeneratorExit:
                            logger.error(f"Failed to set PRAGMA {pragma}")
                finally:
                    cursor.close()

    @asynccontextmanager
    async def _with_session(self):
        """创建原始会话（内部使用）。

        契约：
        - 输入：无。
        - 输出：异步上下文中的会话对象（`AsyncSession` 或 `NoopSession`）。
        - 副作用：在 `use_noop_database` 时返回空实现。
        - 失败语义：会话创建异常透传。

        关键路径：
        1) 若启用 `use_noop_database` 则返回 `NoopSession`。
        2) 否则从 `async_session_maker` 产出真实会话。

        决策：提供内部会话入口但不负责提交。
        问题：部分内部逻辑需要原始会话而非事务包装。
        方案：仅创建会话并由调用方管理提交。
        代价：调用方必须自行处理提交/回滚。
        重评：当统一事务管理完善后移除该入口。
        """
        if self.settings_service.settings.use_noop_database:
            yield NoopSession()
        else:
            # 注意：使用 `async_session_maker` 统一连接池管理。
            async with self.async_session_maker() as session:
                yield session

    async def assign_orphaned_flows_to_superuser(self) -> None:
        """在自动登录启用时将孤儿 `Flow` 分配给超级用户。

        契约：
        - 输入：无。
        - 输出：`None`。
        - 副作用：更新 `Flow.user_id` 与 `Flow.name` 并提交事务。
        - 失败语义：超级用户缺失时抛 `RuntimeError`，其余异常透传。

        关键路径（三步）：
        1) 检查 `AUTO_LOGIN` 并查询无主 `Flow`。
        2) 获取超级用户并生成唯一名称。
        3) 批量更新并提交。

        异常流：超级用户不存在会终止流程。
        性能瓶颈：全量查询无主 `Flow` 与名称去重。

        决策：将无主 `Flow` 统一归属到超级用户。
        问题：自动登录模式下无主数据需要可见的默认归属。
        方案：复用 `SUPERUSER` 账号并去重名称。
        代价：可能改变原始 `Flow` 所属语义。
        重评：当引入系统级共享空间时改为归属到公共空间。

        排障入口：日志关键字 `Assigning orphaned flows`。
        """
        settings_service = get_settings_service()

        if not settings_service.auth_settings.AUTO_LOGIN:
            return

        async with session_scope() as session:
            # 注意：仅处理无主 `Flow`，避免覆盖已归属数据。
            stmt = (
                select(models.Flow)
                .join(models.Folder)
                .where(
                    models.Flow.user_id == None,  # noqa: E711
                    models.Folder.name != STARTER_FOLDER_NAME,
                )
            )
            orphaned_flows = (await session.exec(stmt)).all()

            if not orphaned_flows:
                return

            await logger.adebug("Assigning orphaned flows to the default superuser")

            # 注意：使用配置的 `SUPERUSER` 账号作为归属目标。
            superuser_username = settings_service.auth_settings.SUPERUSER
            superuser = await get_user_by_username(session, superuser_username)

            if not superuser:
                error_message = "Default superuser not found"
                await logger.aerror(error_message)
                raise RuntimeError(error_message)

            # 注意：收集已有名称以避免重名。
            existing_names: set[str] = set(
                (await session.exec(select(models.Flow.name).where(models.Flow.user_id == superuser.id))).all()
            )

            # 注意：为每个无主 `Flow` 生成唯一名称并写入新归属。
            for flow in orphaned_flows:
                flow.user_id = superuser.id
                flow.name = self._generate_unique_flow_name(flow.name, existing_names)
                existing_names.add(flow.name)
                session.add(flow)

            # 注意：统一提交变更。
            await session.commit()
            await logger.adebug("Successfully assigned orphaned flows to the default superuser")

    @staticmethod
    def _generate_unique_flow_name(original_name: str, existing_names: set[str]) -> str:
        """生成不重复的 `Flow` 名称。

        契约：
        - 输入：`original_name` 与已有名称集合 `existing_names`。
        - 输出：不与集合冲突的名称。
        - 副作用：无。
        - 失败语义：名称格式异常时抛 `ValueError`。

        关键路径：
        1) 若原名不冲突直接返回。
        2) 解析末尾序号并递增。
        3) 迭代直到唯一。
        """
        if original_name not in existing_names:
            return original_name

        match = re.search(r"^(.*) \((\d+)\)$", original_name)
        if match:
            base_name, current_number = match.groups()
            new_name = f"{base_name} ({int(current_number) + 1})"
        else:
            new_name = f"{original_name} (1)"

        # 注意：通过递增序号确保唯一性。
        while new_name in existing_names:
            match = re.match(r"^(.*) \((\d+)\)$", new_name)
            if match is not None:
                base_name, current_number = match.groups()
            else:
                error_message = "Invalid format: match is None"
                raise ValueError(error_message)

            new_name = f"{base_name} ({int(current_number) + 1})"

        return new_name

    @staticmethod
    def _check_schema_health(connection) -> bool:
        """校验关键表与列是否存在。

        契约：
        - 输入：同步连接对象 `connection`。
        - 输出：`True` 表示表结构健康。
        - 副作用：记录缺失表/列日志。
        - 失败语义：缺表/缺列时返回 `False`。

        关键路径：
        1) 定义关键表与模型映射。
        2) 校验列集合是否完整。
        3) 记录遗留表并返回结果。
        """
        inspector = inspect(connection)

        model_mapping: dict[str, type[SQLModel]] = {
            "flow": models.Flow,
            "user": models.User,
            "apikey": models.ApiKey,
            # 注意：需要校验时在此补充更多模型。
        }

        # 注意：兼容旧版本遗留表。
        legacy_tables = ["flowstyle"]

        for table, model in model_mapping.items():
            expected_columns = list(model.model_fields.keys())

            try:
                available_columns = [col["name"] for col in inspector.get_columns(table)]
            except sa.exc.NoSuchTableError:
                logger.debug(f"Missing table: {table}")
                return False

            for column in expected_columns:
                if column not in available_columns:
                    logger.debug(f"Missing column: {column} in table {table}")
                    return False

        for table in legacy_tables:
            if table in inspector.get_table_names():
                logger.warning(f"Legacy table exists: {table}")

        return True

    async def check_schema_health(self) -> None:
        """异步触发表结构健康检查。

        契约：
        - 输入：无。
        - 输出：`None`。
        - 副作用：调用 `_check_schema_health` 执行同步检查。
        - 失败语义：检查异常透传。

        关键路径：使用 `engine.begin()` 并 `run_sync` 执行检查。

        决策：通过同步检查函数在异步连接中执行。
        问题：同步检查逻辑无法直接在异步上下文中运行。
        方案：使用 `run_sync` 适配同步检查函数。
        代价：需要额外的同步/异步切换。
        重评：当检查逻辑改为异步时移除 `run_sync`。
        """
        async with self.engine.begin() as conn:
            await conn.run_sync(self._check_schema_health)

    @staticmethod
    def init_alembic(alembic_cfg) -> None:
        """初始化 `Alembic` 版本并升级到最新版本。

        契约：
        - 输入：`alembic_cfg` 配置对象。
        - 输出：`None`。
        - 副作用：写入/更新 `alembic_version` 并执行升级。
        - 失败语义：迁移异常透传。

        关键路径：`ensure_version` 后执行 `upgrade head`。

        决策：初始化后直接升级到 `head`。
        问题：首次启动需保证版本表存在且与模型一致。
        方案：先 `ensure_version` 再全量升级。
        代价：首次迁移耗时增加。
        重评：当支持分阶段迁移时改为按版本升级。
        """
        logger.info("Initializing alembic")
        command.ensure_version(alembic_cfg)
        command.upgrade(alembic_cfg, "head")

    def _run_migrations(self, should_initialize_alembic, fix) -> None:
        """同步执行迁移检查与升级。

        契约：
        - 输入：`should_initialize_alembic` 决定是否初始化版本表；`fix` 控制自动修复。
        - 输出：`None`。
        - 副作用：执行迁移、写入日志文件、可能降级再升级。
        - 失败语义：初始化或迁移差异未修复时抛 `RuntimeError`。

        关键路径（三步）：
        1) 构建 `Alembic` 配置并必要时初始化版本表。
        2) 执行 `check` 并在差异时升级到 `head`。
        3) 可选执行降级/升级修复流程。

        决策：在发现差异时优先升级到 `head`。
        问题：模型与数据库结构不一致会影响运行期读写。
        方案：检查差异并自动执行升级或修复。
        代价：迁移过程可能阻塞启动并增加风险。
        重评：当迁移需人工审批时关闭自动修复流程。
        """
        # 注意：通过 `alembic_version` 表判断是否初始化。
        buffer_context = (
            nullcontext(sys.stdout) if self.alembic_log_to_stdout else self.alembic_log_path.open("w", encoding="utf-8")  # type: ignore[union-attr]
        )
        with buffer_context as buffer:
            alembic_cfg = Config(stdout=buffer)
            alembic_cfg.set_main_option("script_location", str(self.script_location))
            alembic_cfg.set_main_option("sqlalchemy.url", self.database_url.replace("%", "%%"))

            if should_initialize_alembic:
                try:
                    self.init_alembic(alembic_cfg)
                except Exception as exc:
                    msg = "Error initializing alembic"
                    logger.exception(msg)
                    raise RuntimeError(msg) from exc
            else:
                logger.debug("Alembic initialized")

            try:
                buffer.write(f"{datetime.now(tz=timezone.utc).astimezone().isoformat()}: Checking migrations\n")
                command.check(alembic_cfg)
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"Error checking migrations: {exc}")
                if isinstance(exc, util.exc.CommandError | util.exc.AutogenerateDiffsDetected):
                    command.upgrade(alembic_cfg, "head")
                    time.sleep(3)

            try:
                buffer.write(f"{datetime.now(tz=timezone.utc).astimezone()}: Checking migrations\n")
                command.check(alembic_cfg)
            except util.exc.AutogenerateDiffsDetected as exc:
                logger.exception("Error checking migrations")
                if not fix:
                    msg = f"There's a mismatch between the models and the database.\n{exc}"
                    raise RuntimeError(msg) from exc

            if fix:
                self.try_downgrade_upgrade_until_success(alembic_cfg)

    async def run_migrations(self, *, fix=False) -> None:
        """异步触发迁移流程。

        契约：
        - 输入：`fix` 控制是否自动修复迁移差异。
        - 输出：`None`。
        - 副作用：可能初始化 `Alembic` 并执行迁移。
        - 失败语义：迁移异常透传。

        关键路径：
        1) 检查 `alembic_version` 是否存在。
        2) 在线程池中执行 `_run_migrations`。

        决策：在异步上下文中使用 `to_thread` 调用同步迁移。
        问题：`Alembic` `API` 为同步，直接调用会阻塞事件循环。
        方案：迁移放入线程池执行。
        代价：线程池占用与日志输出顺序可能变化。
        重评：当 `Alembic` 提供异步 `API` 时改为原生异步执行。
        """
        should_initialize_alembic = False
        async with session_scope() as session:
            # 注意：查询 `alembic_version` 不存在会抛错。
            try:
                await session.exec(text("SELECT * FROM alembic_version"))
            except Exception:  # noqa: BLE001
                await logger.adebug("Alembic not initialized")
                should_initialize_alembic = True
        await asyncio.to_thread(self._run_migrations, should_initialize_alembic, fix)

    @staticmethod
    def try_downgrade_upgrade_until_success(alembic_cfg, retries=5) -> None:
        """在迁移差异时尝试降级后再升级。

        契约：
        - 输入：`alembic_cfg` 与 `retries` 次数上限。
        - 输出：`None`。
        - 副作用：执行多次 `downgrade`/`upgrade`。
        - 失败语义：超过重试次数仍失败时异常透传。

        关键路径：
        1) 循环执行 `check`。
        2) 发现差异时按步长降级。
        3) 再升级到 `head`。

        决策：按步长逐步回退再升级。
        问题：自动生成差异导致迁移无法通过检查。
        方案：逐步降级后重跑升级。
        代价：可能丢失中间迁移状态。
        重评：当迁移差异需要人工处理时禁用该逻辑。
        """
        # 注意：按 `-1`、`-2` 逐步回退直至成功或耗尽重试。
        for i in range(1, retries + 1):
            try:
                command.check(alembic_cfg)
                break
            except util.exc.AutogenerateDiffsDetected:
                # 注意：发现差异时执行降级再升级。
                logger.warning("AutogenerateDiffsDetected")
                command.downgrade(alembic_cfg, f"-{i}")
                # 注意：等待数据库完成状态切换。
                time.sleep(3)
                command.upgrade(alembic_cfg, "head")

    async def run_migrations_test(self):
        """测试用迁移校验。

        契约：
        - 输入：无。
        - 输出：`TableResults` 列表。
        - 副作用：读取数据库表结构。
        - 失败语义：查询异常透传。

        关键路径：
        1) 收集所有 `SQLModel` 子类。
        2) 在连接上下文中逐表校验。

        决策：仅在测试环境暴露表结构校验结果。
        问题：需要验证模型与数据库列是否一致。
        方案：遍历模型并调用 `check_table`。
        代价：检查成本随模型数量线性增长。
        重评：当迁移检查在 CI 中固定执行时可移出运行时。
        """
        # 注意：仅用于测试环境的结构一致性检查。
        sql_models = [
            model for model in models.__dict__.values() if isinstance(model, type) and issubclass(model, SQLModel)
        ]
        # 注意：使用 `engine.begin()` 确保连接正确释放。
        async with self.engine.begin() as conn:
            return [
                TableResults(sql_model.__tablename__, await conn.run_sync(self.check_table, sql_model))
                for sql_model in sql_models
            ]

    @staticmethod
    def check_table(connection, model):
        """检查单表是否包含预期列。

        契约：
        - 输入：同步连接与 `SQLModel` 模型类型。
        - 输出：`Result` 列表。
        - 副作用：记录缺失表/列日志。
        - 失败语义：缺表时返回 `success=False` 记录。

        关键路径：
        1) 读取实际列清单。
        2) 对比预期列并记录结果。

        决策：以模型字段集作为权威列清单。
        问题：数据库列可能偏离模型定义导致运行期错误。
        方案：以 `model.__fields__` 作为预期列来源。
        代价：历史冗余列会被视为异常。
        重评：当允许向后兼容字段时改为白名单过滤。
        """
        results = []
        inspector = inspect(connection)
        table_name = model.__tablename__
        expected_columns = list(model.__fields__.keys())
        available_columns = []
        try:
            available_columns = [col["name"] for col in inspector.get_columns(table_name)]
            results.append(Result(name=table_name, type="table", success=True))
        except sa.exc.NoSuchTableError:
            logger.exception(f"Missing table: {table_name}")
            results.append(Result(name=table_name, type="table", success=False))

        for column in expected_columns:
            if column not in available_columns:
                logger.error(f"Missing column: {column} in table {table_name}")
                results.append(Result(name=column, type="column", success=False))
            else:
                results.append(Result(name=column, type="column", success=True))
        return results

    @staticmethod
    def _create_db_and_tables(connection) -> None:
        """创建数据库表结构（同步）。

        契约：
        - 输入：同步连接对象。
        - 输出：`None`。
        - 副作用：创建缺失表。
        - 失败语义：创建失败抛 `RuntimeError`。

        关键路径：
        1) 读取现有表并判断是否已齐全。
        2) 遍历 `SQLModel.metadata` 创建表。
        3) 再次校验关键表存在性。
        """
        from sqlalchemy import inspect

        inspector = inspect(connection)
        table_names = inspector.get_table_names()
        current_tables = ["flow", "user", "apikey", "folder", "message", "variable", "transaction", "vertex_build"]

        if table_names and all(table in table_names for table in current_tables):
            logger.debug("Database and tables already exist")
            return

        logger.debug("Creating database and tables")

        for table in SQLModel.metadata.sorted_tables:
            try:
                table.create(connection, checkfirst=True)
            except OperationalError as oe:
                logger.warning(f"Table {table} already exists, skipping. Exception: {oe}")
            except Exception as exc:
                msg = f"Error creating table {table}"
                logger.exception(msg)
                raise RuntimeError(msg) from exc

        # 注意：二次校验关键表存在性，避免部分表创建失败。
        inspector = inspect(connection)
        table_names = inspector.get_table_names()
        for table in current_tables:
            if table not in table_names:
                logger.error("Something went wrong creating the database and tables.")
                logger.error("Please check your database settings.")
                msg = "Something went wrong creating the database and tables."
                raise RuntimeError(msg)

        logger.debug("Database and tables created successfully")

    @retry(wait=wait_fixed(2), stop=stop_after_attempt(10))
    async def create_db_and_tables_with_retry(self) -> None:
        """带重试创建数据库与表结构。

        契约：
        - 输入：无。
        - 输出：`None`。
        - 副作用：创建表并可能等待重试。
        - 失败语义：超过重试次数后抛异常。

        关键路径：调用 `create_db_and_tables` 并由重试装饰器控制。

        决策：对建表流程增加固定间隔重试。
        问题：数据库在启动初期可能不可用。
        方案：最多重试 10 次，每次间隔 2 秒。
        代价：启动耗时增加。
        重评：当连接稳定后可关闭重试。
        """
        await self.create_db_and_tables()

    async def create_db_and_tables(self) -> None:
        """创建数据库与表结构（异步入口）。

        契约：
        - 输入：无。
        - 输出：`None`。
        - 副作用：在连接中执行表创建。
        - 失败语义：建表异常透传。

        关键路径：使用 `engine.begin()` 并 `run_sync` 调用 `_create_db_and_tables`。

        决策：通过同步建表函数在异步连接中执行。
        问题：`SQLModel.metadata` 建表为同步 API。
        方案：使用 `run_sync` 适配同步建表逻辑。
        代价：建表过程在单线程中执行。
        重评：当建表支持异步时改为原生异步执行。
        """
        async with self.engine.begin() as conn:
            await conn.run_sync(self._create_db_and_tables)

    async def teardown(self) -> None:
        """释放数据库资源并清理超级用户。

        契约：
        - 输入：无。
        - 输出：`None`。
        - 副作用：可能删除默认超级用户并关闭引擎。
        - 失败语义：清理异常仅记录日志，不中断 `dispose`。

        关键路径：
        1) 如启用自动登录则清理超级用户。
        2) 释放引擎资源。

        决策：清理失败不阻断资源释放。
        问题：清理异常可能阻止进程退出。
        方案：捕获异常并记录，再调用 `dispose`。
        代价：失败可能遗留用户数据。
        重评：当清理需强一致时改为失败即终止。
        """
        await logger.adebug("Tearing down database")
        try:
            settings_service = get_settings_service()
            # 注意：仅在启用自动登录时清理默认超级用户。
            async with session_scope() as session:
                await teardown_superuser(settings_service, session)
        except Exception:  # noqa: BLE001
            await logger.aexception("Error tearing down database")
        await self.engine.dispose()
