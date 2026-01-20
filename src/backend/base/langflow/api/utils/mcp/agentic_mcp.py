"""
模块名称：`Agentic` `MCP` 自动配置与变量初始化

本模块用于在启用 `agentic_experience` 时批量配置/移除 `MCP` 服务器，并补齐 `Agentic`
全局变量。主要功能包括：
- 写入或移除固定服务器 `langflow-agentic`（命令 `python -m langflow.agentic.mcp`）
- 初始化 `FLOW_ID` / `COMPONENT_ID` / `FIELD_NAME` 等上下文变量
- 登录或创建时为单用户补齐变量集

关键组件：`auto_configure_agentic_mcp_server` / `remove_agentic_mcp_server` /
`initialize_agentic_global_variables` / `initialize_agentic_user_variables`

设计背景：`Agentic` 客户端需要稳定入口与上下文变量，否则工具不可见或缺少上下文。
使用场景：服务启动批量补齐、用户登录/创建时补齐变量。
注意事项：失败以日志记录为主，不抛给调用方；用户规模大时会放大数据库会话压力。
"""

import sys
from uuid import UUID

from fastapi import HTTPException
from lfx.log.logger import logger
from lfx.services.deps import get_settings_service
from sqlalchemy import exc as sqlalchemy_exc
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from langflow.api.v2.mcp import get_server_list, update_server
from langflow.services.database.models.user.model import User
from langflow.services.deps import get_service, get_variable_service
from langflow.services.schema import ServiceType
from langflow.services.variable.constants import CREDENTIAL_TYPE, GENERIC_TYPE


async def auto_configure_agentic_mcp_server(session: AsyncSession) -> None:
    """为所有用户补齐 `langflow-agentic` `MCP` 服务器配置。

    契约：
    - 输入：`session` 用于读取 `User` 与写入 `MCP` 配置。
    - 输出：`None`；副作用为调用 `update_server` 写入用户配置。
    - 失败语义：单用户失败仅记录日志并继续；读取现有配置失败则跳过该用户以避免重复写入。

    关键路径（三步）：
    1) 校验 `agentic_experience` 开关并拉取全部用户。
    2) 调用 `get_server_list` 检测是否已存在同名服务器。
    3) 生成 `python -m langflow.agentic.mcp` 配置并写入。

    异常流：`get_server_list`/`update_server` 抛 `HTTPException` 或 `SQLAlchemyError` 时记录并跳过。
    性能瓶颈：全量扫描 `User` + 逐用户写入配置。

    决策：全量扫描并逐用户写入固定 `server_name`。
    问题：需要一次性为所有存量用户开放 `Agentic` 工具入口。
    方案：遍历 `User` 并调用 `update_server` 写入 `langflow-agentic`。
    代价：启动时会增加全表读取与多次写入开销。
    重评：当用户规模 >100000 或支持增量事件时改为分批/异步。

    排障入口：日志关键字 `Agentic MCP server` / `skipping` / `added`。
    """
    settings_service = get_settings_service()

    # 注意：关闭开关时禁止写入，避免为不支持的环境暴露 `Agentic` 工具入口。
    if not settings_service.settings.agentic_experience:
        await logger.adebug("Agentic experience disabled, skipping agentic MCP server configuration")
        return

    await logger.ainfo("Auto-configuring Langflow Agentic MCP server for all users...")

    try:
        # 注意：全量扫描用户会放大数据库压力，避免在高频路径调用。
        users = (await session.exec(select(User))).all()
        await logger.adebug(f"Found {len(users)} users in the system")

        if not users:
            await logger.adebug("No users found, skipping agentic MCP server configuration")
            return

        storage_service = get_service(ServiceType.STORAGE_SERVICE)

        # 注意：`server_name` 为固定值，重复写入会覆盖同名配置。
        server_name = "langflow-agentic"
        python_executable = sys.executable
        server_config = {
            "command": python_executable,
            "args": ["-m", "langflow.agentic.mcp"],
            "metadata": {
                "description": "Langflow Agentic MCP server providing tools for flow/component operations, "
                "template search, and graph visualization",
                "auto_configured": True,
                "langflow_internal": True,
            },
        }

        servers_added = 0
        servers_skipped = 0

        for user in users:
            try:
                await logger.adebug(f"Configuring agentic MCP server for user: {user.username}")

                try:
                    server_list = await get_server_list(user, session, storage_service, settings_service)
                    server_exists = server_name in server_list.get("mcpServers", {})

                    if server_exists:
                        await logger.adebug(f"Agentic MCP server already exists for user {user.username}, skipping")
                        servers_skipped += 1
                        continue

                except (HTTPException, sqlalchemy_exc.SQLAlchemyError) as e:
                    # 注意：无法确认现有配置时跳过该用户，避免产生重复或覆盖他人配置。
                    await logger.awarning(
                        f"Could not check existing servers for user {user.username}: {e}. "
                        "Skipping to avoid potential duplicates."
                    )
                    servers_skipped += 1
                    continue

                await update_server(
                    server_name=server_name,
                    server_config=server_config,
                    current_user=user,
                    session=session,
                    storage_service=storage_service,
                    settings_service=settings_service,
                )

                servers_added += 1
                await logger.adebug(f"Added agentic MCP server for user: {user.username}")

            except (HTTPException, sqlalchemy_exc.SQLAlchemyError) as e:
                await logger.aexception(f"Failed to configure agentic MCP server for user {user.username}: {e}")
                continue

        await logger.ainfo(
            f"Agentic MCP server configuration complete: {servers_added} added, {servers_skipped} skipped"
        )

    except (
        HTTPException,
        sqlalchemy_exc.SQLAlchemyError,
        OSError,
        PermissionError,
        FileNotFoundError,
        RuntimeError,
        ValueError,
        AttributeError,
    ) as e:
        await logger.aexception(f"Error during agentic MCP server auto-configuration: {e}")


async def remove_agentic_mcp_server(session: AsyncSession) -> None:
    """为所有用户移除 `langflow-agentic` `MCP` 服务器配置。

    契约：
    - 输入：`session` 用于枚举用户并写入空配置。
    - 输出：`None`；副作用为调用 `update_server` 触发删除。
    - 失败语义：单用户失败记录日志并继续，不影响其他用户。

    关键路径（三步）：
    1) 拉取全部用户。
    2) 对每个用户写入空配置以触发删除。
    3) 汇总删除数量并记录日志。

    异常流：`update_server` 抛 `HTTPException`/`SQLAlchemyError` 时记录并继续。
    性能瓶颈：全量扫描 `User` + 逐用户写入空配置。

    决策：通过写入空配置触发删除。
    问题：需要复用 `update_server` 入口而不新增删除接口。
    方案：传 `server_config={}` 触发删除语义。
    代价：依赖 `update_server` 行为，变更时需同步调整。
    重评：当提供显式删除接口或批量删除 `API` 时改用新接口。

    排障入口：日志关键字 `Removed agentic MCP server`。
    """
    await logger.ainfo("Removing Langflow Agentic MCP server from all users...")

    try:
        users = (await session.exec(select(User))).all()

        if not users:
            await logger.adebug("No users found")
            return

        storage_service = get_service(ServiceType.STORAGE_SERVICE)
        settings_service = get_settings_service()

        server_name = "langflow-agentic"
        servers_removed = 0

        for user in users:
            try:
                # 注意：空配置会被 `update_server` 视为删除请求。
                await update_server(
                    server_name=server_name,
                    server_config={},
                    current_user=user,
                    session=session,
                    storage_service=storage_service,
                    settings_service=settings_service,
                )

                servers_removed += 1
                await logger.adebug(f"Removed agentic MCP server for user: {user.username}")

            except (HTTPException, sqlalchemy_exc.SQLAlchemyError) as e:
                await logger.adebug(f"Could not remove agentic MCP server for user {user.username}: {e}")
                continue

        await logger.ainfo(f"Removed agentic MCP server from {servers_removed} users")

    except (
        HTTPException,
        sqlalchemy_exc.SQLAlchemyError,
        OSError,
        PermissionError,
        FileNotFoundError,
        RuntimeError,
        ValueError,
        AttributeError,
    ) as e:
        await logger.aexception(f"Error removing agentic MCP server: {e}")


async def initialize_agentic_global_variables(session: AsyncSession) -> None:
    """为所有用户补齐 `Agentic` 全局变量。

    契约：
    - 输入：`session` 用于读取用户与创建变量。
    - 输出：`None`；副作用为创建缺失变量（`FLOW_ID`/`COMPONENT_ID`/`FIELD_NAME`，类型 `GENERIC_TYPE`）。
    - 失败语义：单变量创建失败记录异常并继续下一个变量/用户。

    关键路径（三步）：
    1) 校验 `agentic_experience` 开关并拉取用户。
    2) 查询用户已有变量集合。
    3) 为缺失变量写入默认值空字符串。

    异常流：`create_variable` 抛 `HTTPException`/`SQLAlchemyError` 时记录并继续。
    性能瓶颈：全量扫描 `User` + 每用户多变量写入。

    决策：使用 `GENERIC_TYPE` 并以空字符串作为默认值。
    问题：需要无侵入地为所有用户补齐上下文变量。
    方案：仅在缺失时创建变量，默认值为空字符串。
    代价：无法区分“未设置”与“显式空值”。
    重评：当需要区分状态或提供类型校验时引入枚举/占位标记。

    排障入口：日志关键字 `agentic variables` / `Created agentic variable`。
    """
    settings_service = get_settings_service()

    # 注意：禁用时不创建变量，避免前端误展示不可用能力。
    if not settings_service.settings.agentic_experience:
        await logger.adebug("Agentic experience disabled, skipping agentic variables initialization")
        return

    await logger.ainfo("Initializing agentic global variables for all users...")

    try:
        # 注意：全量扫描用户会放大数据库压力，避免在高频路径调用。
        users = (await session.exec(select(User))).all()
        await logger.adebug(f"Found {len(users)} users for agentic variables initialization")

        if not users:
            await logger.adebug("No users found, skipping agentic variables initialization")
            return

        variable_service = get_variable_service()

        # 注意：默认值为空字符串，依赖上层在执行前显式填充上下文。
        agentic_variables = {
            "FLOW_ID": "",
            "COMPONENT_ID": "",
            "FIELD_NAME": "",
        }

        variables_created = 0
        variables_skipped = 0

        for user in users:
            try:
                await logger.adebug(f"Initializing agentic variables for user: {user.username}")

                existing_vars = await variable_service.list_variables(user.id, session)

                for var_name, default_value in agentic_variables.items():
                    try:
                        if var_name not in existing_vars:
                            await variable_service.create_variable(
                                user_id=user.id,
                                name=var_name,
                                value=default_value,
                                default_fields=[],
                                type_=GENERIC_TYPE,
                                session=session,
                            )
                            variables_created += 1
                            await logger.adebug(f"Created agentic variable {var_name} for user {user.username}")
                        else:
                            variables_skipped += 1
                            await logger.adebug(
                                f"Agentic variable {var_name} already exists for user {user.username}, skipping"
                            )
                    except (
                        HTTPException,
                        sqlalchemy_exc.SQLAlchemyError,
                        OSError,
                        PermissionError,
                        FileNotFoundError,
                        RuntimeError,
                        ValueError,
                        AttributeError,
                    ) as e:
                        await logger.aexception(
                            f"Error creating agentic variable {var_name} for user {user.username}: {e}"
                        )
                        continue

            except (
                HTTPException,
                sqlalchemy_exc.SQLAlchemyError,
                OSError,
                PermissionError,
                FileNotFoundError,
                RuntimeError,
                ValueError,
                AttributeError,
            ) as e:
                await logger.aexception(f"Failed to initialize agentic variables for user {user.username}: {e}")
                continue

        await logger.ainfo(
            f"Agentic variables initialization complete: {variables_created} created, {variables_skipped} skipped"
        )

    except (
        HTTPException,
        sqlalchemy_exc.SQLAlchemyError,
        OSError,
        PermissionError,
        FileNotFoundError,
        RuntimeError,
        ValueError,
        AttributeError,
    ) as e:
        await logger.aexception(f"Error during agentic variables initialization: {e}")


async def initialize_agentic_user_variables(user_id: UUID | str, session: AsyncSession) -> None:
    """为单个用户补齐 `Agentic` 变量集合。

    契约：
    - 输入：`user_id` 支持 `UUID` 或字符串；`session` 用于持久化。
    - 输出：`None`；副作用为创建缺失变量，类型使用 `CREDENTIAL_TYPE`。
    - 失败语义：单变量失败记录异常并继续；未启用 `agentic_experience` 时直接返回。

    关键路径（三步）：
    1) 读取 `AGENTIC_VARIABLES` 与默认值。
    2) 查询用户现有变量集合。
    3) 为缺失项写入默认值。

    异常流：`create_variable` 抛 `HTTPException`/`SQLAlchemyError` 时记录并继续。
    性能瓶颈：登录/创建流程额外一次变量查询与逐项写入。

    决策：以 `AGENTIC_VARIABLES` 清单为唯一来源并写入 `CREDENTIAL_TYPE`。
    问题：需要与设置服务保持变量集合一致且支持凭据级别保护。
    方案：读取常量清单并对缺失项执行创建。
    代价：新增变量需同步发布设置常量。
    重评：当变量集需动态扩展时改为配置驱动或迁移脚本。

    排障入口：日志关键字 `Created agentic variable` / `already exists`。
    """
    settings_service = get_settings_service()

    # 注意：禁用时不创建变量，避免产生不可达的敏感字段。
    if not settings_service.settings.agentic_experience:
        await logger.adebug(f"Agentic experience disabled, skipping agentic variables for user {user_id}")
        return

    await logger.adebug(f"Initializing agentic variables for user {user_id}")

    try:
        variable_service = get_variable_service()

        from lfx.services.settings.constants import AGENTIC_VARIABLES, DEFAULT_AGENTIC_VARIABLE_VALUE

        # 注意：默认值为空字符串，与 `UI` 占位语义一致。
        agentic_variables = dict.fromkeys(AGENTIC_VARIABLES, DEFAULT_AGENTIC_VARIABLE_VALUE)
        logger.adebug(f"Agentic variables: {agentic_variables}")

        existing_vars = await variable_service.list_variables(user_id, session)

        for var_name, default_value in agentic_variables.items():
            logger.adebug(f"Checking if agentic variable {var_name} exists for user {user_id}")
            if var_name not in existing_vars:
                try:
                    await variable_service.create_variable(
                        user_id=user_id,
                        name=var_name,
                        value=default_value,
                        default_fields=[],
                        type_=CREDENTIAL_TYPE,
                        session=session,
                    )
                    await logger.adebug(f"Created agentic variable {var_name} for user {user_id}")
                except (
                    HTTPException,
                    sqlalchemy_exc.SQLAlchemyError,
                    OSError,
                    PermissionError,
                    FileNotFoundError,
                    RuntimeError,
                    ValueError,
                    AttributeError,
                ) as e:
                    await logger.aexception(f"Error creating agentic variable {var_name} for user {user_id}: {e}")
            else:
                await logger.adebug(f"Agentic variable {var_name} already exists for user {user_id}, skipping")

    except (
        HTTPException,
        sqlalchemy_exc.SQLAlchemyError,
        OSError,
        PermissionError,
        FileNotFoundError,
        RuntimeError,
        ValueError,
        AttributeError,
    ) as e:
        await logger.aexception(f"Error initializing agentic variables for user {user_id}: {e}")
