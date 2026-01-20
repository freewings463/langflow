"""
模块名称：MCP 配置管理 API

本模块通过文件存储维护 MCP 服务器配置，并提供增删改查与工具计数检查。
主要功能包括：
- 读写用户的 MCP 配置文件
- 兼容旧文件名迁移
- 并发检查服务器工具数量并返回状态
- 更新缓存以避免旧配置

关键组件：
- `get_server_list` / `get_server`
- `update_server` / `add_server` / `delete_server`
- `get_servers`：并发检查工具数量

设计背景：MCP 配置以用户私有文件保存，便于无数据库场景迁移。
注意事项：配置文件损坏时会重建空配置；工具检查会创建并断开子进程。
"""

import contextlib
import json
from io import BytesIO
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from lfx.base.agents.utils import safe_cache_get, safe_cache_set
from lfx.base.mcp.util import update_tools

from langflow.api.utils import CurrentActiveUser, DbSession
from langflow.api.v2.files import (
    MCP_SERVERS_FILE,
    delete_file,
    download_file,
    edit_file_name,
    get_file_by_name,
    get_mcp_file,
    upload_user_file,
)
from langflow.logging import logger
from langflow.services.deps import get_settings_service, get_shared_component_cache_service, get_storage_service
from langflow.services.settings.service import SettingsService
from langflow.services.storage.service import StorageService

router = APIRouter(tags=["MCP"], prefix="/mcp")


async def upload_server_config(
    server_config: dict,
    current_user: CurrentActiveUser,
    session: DbSession,
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
):
    """将 MCP 配置写入用户文件。

    契约：接收配置字典并委托文件上传接口。
    副作用：写入存储与 DB 元数据。
    失败语义：上传失败向上抛 `HTTPException`。
    """
    content_str = json.dumps(server_config)
    content_bytes = content_str.encode("utf-8")
    file_obj = BytesIO(content_bytes)

    mcp_file = await get_mcp_file(current_user, extension=True)
    upload_file = UploadFile(file=file_obj, filename=mcp_file, size=len(content_str))

    return await upload_user_file(
        file=upload_file,
        session=session,
        current_user=current_user,
        storage_service=storage_service,
        settings_service=settings_service,
    )


async def get_server_list(
    current_user: CurrentActiveUser,
    session: DbSession,
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
):
    """获取 MCP 服务器配置列表。

    契约：返回包含 `mcpServers` 的配置字典。
    关键路径（三步）：
    1) 兼容旧文件名并必要时迁移
    2) 读取配置文件内容
    3) 解析 JSON 返回配置
    失败语义：配置损坏时返回 500；文件缺失时重建空配置。

    决策：配置文件缺失/损坏时重建空配置
    问题：配置文件可能被删除或损坏
    方案：删除旧记录后写入空配置
    代价：原配置不可恢复
    重评：若引入配置备份或版本时改为恢复
    """
    # 注意：兼容旧格式文件名 `_mcp_servers`
    mcp_file = await get_mcp_file(current_user)
    old_format_config_file = await get_file_by_name(MCP_SERVERS_FILE, current_user, session)
    if old_format_config_file:
        await edit_file_name(old_format_config_file.id, mcp_file, current_user, session)

    server_config_file = await get_file_by_name(mcp_file, current_user, session)

    try:
        server_config_bytes = await download_file(
            server_config_file.id if server_config_file else None,
            current_user,
            session,
            storage_service=storage_service,
            return_content=True,
        )
    except (FileNotFoundError, HTTPException):
        # 注意：存储缺失时视为 DB 记录过期，删除后重建
        if server_config_file:
            with contextlib.suppress(Exception):
                await delete_file(server_config_file.id, current_user, session, storage_service)

        await upload_server_config(
            {"mcpServers": {}},
            current_user,
            session,
            storage_service=storage_service,
            settings_service=settings_service,
        )

        mcp_file = await get_mcp_file(current_user)
        server_config_file = await get_file_by_name(mcp_file, current_user, session)
        if not server_config_file:
            raise HTTPException(status_code=500, detail="Failed to create MCP Servers configuration file") from None

        server_config_bytes = await download_file(
            server_config_file.id,
            current_user,
            session,
            storage_service=storage_service,
            return_content=True,
        )

    try:
        servers = json.loads(server_config_bytes)
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Invalid server configuration file format.") from None

    return servers


async def get_server(
    server_name: str,
    current_user: CurrentActiveUser,
    session: DbSession,
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
    server_list: dict | None = None,
):
    """获取单个 MCP 服务器配置。"""
    if server_list is None:
        server_list = await get_server_list(current_user, session, storage_service, settings_service)

    if server_name not in server_list["mcpServers"]:
        return None

    return server_list["mcpServers"][server_name]


@router.get("/servers")
async def get_servers(
    current_user: CurrentActiveUser,
    session: DbSession,
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
    *,
    action_count: bool | None = None,
):
    """获取服务器列表，可选返回工具数量与模式。

    契约：`action_count=False` 仅返回名称；否则返回模式与工具数量。
    关键路径（三步）：
    1) 读取配置文件
    2) 并发检查各服务器工具列表
    3) 汇总结果返回
    失败语义：单个服务器失败以 `error` 字段返回，不中断整体。

    决策：并发检查工具数量
    问题：串行检查耗时长且易超时
    方案：`asyncio.gather` 并发调用
    代价：并发创建子进程/连接，资源峰值上升
    重评：当服务器数量很大时考虑限流
    """
    import asyncio

    from lfx.base.mcp.util import MCPStdioClient, MCPStreamableHttpClient

    server_list = await get_server_list(current_user, session, storage_service, settings_service)

    if not action_count:
        # 注意：不做工具检查时仅返回名称
        return [{"name": server_name, "mode": None, "toolsCount": None} for server_name in server_list["mcpServers"]]

    # 注意：并发检查工具数量，避免串行等待
    async def check_server(server_name: str) -> dict:
        server_info: dict[str, str | int | None] = {"name": server_name, "mode": None, "toolsCount": None}
        # 注意：手动创建客户端，确保最终释放子进程
        mcp_stdio_client = MCPStdioClient()
        mcp_streamable_http_client = MCPStreamableHttpClient()
        try:
            mode, tool_list, _ = await update_tools(
                server_name=server_name,
                server_config=server_list["mcpServers"][server_name],
                mcp_stdio_client=mcp_stdio_client,
                mcp_streamable_http_client=mcp_streamable_http_client,
            )
            server_info["mode"] = mode.lower()
            server_info["toolsCount"] = len(tool_list)
            if len(tool_list) == 0:
                server_info["error"] = "No tools found"
        except ValueError as e:
            # 配置校验/URL 非法
            await logger.aerror(f"Configuration error for server {server_name}: {e}")
            server_info["error"] = f"Configuration error: {e}"
        except ConnectionError as e:
            # 网络连接失败
            await logger.aerror(f"Connection error for server {server_name}: {e}")
            server_info["error"] = f"Connection failed: {e}"
        except (TimeoutError, asyncio.TimeoutError) as e:
            # 超时
            await logger.aerror(f"Timeout error for server {server_name}: {e}")
            server_info["error"] = "Timeout when checking server tools"
        except OSError as e:
            # 进程执行/文件访问错误
            await logger.aerror(f"System error for server {server_name}: {e}")
            server_info["error"] = f"System error: {e}"
        except (KeyError, TypeError) as e:
            # 配置数据解析错误
            await logger.aerror(f"Data error for server {server_name}: {e}")
            server_info["error"] = f"Configuration data error: {e}"
        except (RuntimeError, ProcessLookupError, PermissionError) as e:
            # 运行期/进程权限错误
            await logger.aerror(f"Runtime error for server {server_name}: {e}")
            server_info["error"] = f"Runtime error: {e}"
        except Exception as e:  # noqa: BLE001
            # 兜底异常（含 ExceptionGroup）
            if hasattr(e, "exceptions") and e.exceptions:
                # 注意：取首个底层异常用于更可读的提示
                underlying_error = e.exceptions[0]
                if hasattr(underlying_error, "exceptions"):
                    await logger.aerror(
                        f"Error checking server {server_name}: {underlying_error}, {underlying_error.exceptions}"
                    )
                    underlying_error = underlying_error.exceptions[0]
                else:
                    await logger.aexception(f"Error checking server {server_name}: {underlying_error}")
                server_info["error"] = f"Error loading server: {underlying_error}"
            else:
                await logger.aexception(f"Error checking server {server_name}: {e}")
                server_info["error"] = f"Error loading server: {e}"
        finally:
            # 注意：必须断开连接，避免 mcp-proxy 子进程泄漏
            await mcp_stdio_client.disconnect()
            await mcp_streamable_http_client.disconnect()
        return server_info

    # 注意：并发执行所有服务器检查
    tasks = [check_server(server) for server in server_list["mcpServers"]]
    return await asyncio.gather(*tasks, return_exceptions=True)


@router.get("/servers/{server_name}")
async def get_server_endpoint(
    server_name: str,
    current_user: CurrentActiveUser,
    session: DbSession,
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
):
    """获取指定服务器配置。

    契约：返回配置字典或 `None`。
    失败语义：读取配置失败时抛 `HTTPException(500)`。

    决策：复用 `get_server` 统一读取逻辑
    问题：避免重复处理文件读取与兼容逻辑
    方案：路由层直接调用 `get_server`
    代价：路由层缺少定制化处理
    重评：若需要额外鉴权或缓存时调整
    """
    return await get_server(server_name, current_user, session, storage_service, settings_service)


async def update_server(
    server_name: str,
    server_config: dict,
    current_user: CurrentActiveUser,
    session: DbSession,
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
    *,
    check_existing: bool = False,
    delete: bool = False,
):
    """更新/删除 MCP 服务器配置并刷新缓存。

    契约：根据 `check_existing`/`delete` 行为更新配置并返回最新配置。
    副作用：写入配置文件、删除旧文件、清理共享缓存。
    失败语义：重复创建或找不到目标时返回 500。

    决策：更新采用“删旧文件再写新文件”
    问题：配置文件为单体对象，无法原地局部更新
    方案：删除旧配置后整体写入
    代价：更新过程中存在短暂不可读窗口
    重评：若支持原子写入或版本化文件再优化
    """
    server_list = await get_server_list(current_user, session, storage_service, settings_service)

    # 注意：创建模式下禁止覆盖
    if check_existing and server_name in server_list["mcpServers"]:
        raise HTTPException(status_code=500, detail="Server already exists.")

    if delete:
        if server_name in server_list["mcpServers"]:
            del server_list["mcpServers"][server_name]
        else:
            raise HTTPException(status_code=500, detail="Server not found.")
    else:
        server_list["mcpServers"][server_name] = server_config

    mcp_file = await get_mcp_file(current_user)
    server_config_file = await get_file_by_name(mcp_file, current_user, session)

    # 注意：配置更新采用“删旧文件再写新文件”
    if server_config_file:
        await delete_file(server_config_file.id, current_user, session, storage_service)

    await upload_server_config(
        server_list, current_user, session, storage_service=storage_service, settings_service=settings_service
    )

    shared_component_cache_service = get_shared_component_cache_service()
    # 注意：清理共享缓存，避免返回旧配置
    servers = safe_cache_get(shared_component_cache_service, "servers", {})
    if isinstance(servers, dict):
        if server_name in servers:
            del servers[server_name]
        safe_cache_set(shared_component_cache_service, "servers", servers)

    return await get_server(
        server_name,
        current_user,
        session,
        storage_service,
        settings_service,
        server_list=server_list,
    )


@router.post("/servers/{server_name}")
async def add_server(
    server_name: str,
    server_config: dict,
    current_user: CurrentActiveUser,
    session: DbSession,
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
):
    """新增 MCP 服务器配置。

    契约：同名已存在时返回 500；成功返回最新配置。
    失败语义：写入配置失败抛出 500。

    决策：复用 `update_server` 统一处理逻辑
    问题：新增/更新/删除需要共享文件写入与缓存清理
    方案：调用 `update_server(check_existing=True)`
    代价：错误码与更新逻辑保持一致
    重评：若新增需返回 409 时分离实现
    """
    return await update_server(
        server_name,
        server_config,
        current_user,
        session,
        storage_service,
        settings_service,
        check_existing=True,
    )


@router.patch("/servers/{server_name}")
async def update_server_endpoint(
    server_name: str,
    server_config: dict,
    current_user: CurrentActiveUser,
    session: DbSession,
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
):
    """更新 MCP 服务器配置。

    契约：覆盖指定服务器配置并返回最新配置。
    失败语义：配置写入失败抛 500。

    决策：复用 `update_server` 统一处理逻辑
    问题：避免多处实现导致不一致
    方案：直接调用 `update_server`
    代价：路由层缺少差异化控制
    重评：若需要审计或校验策略时拆分
    """
    return await update_server(
        server_name,
        server_config,
        current_user,
        session,
        storage_service,
        settings_service,
    )


@router.delete("/servers/{server_name}")
async def delete_server(
    server_name: str,
    current_user: CurrentActiveUser,
    session: DbSession,
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
):
    """删除 MCP 服务器配置。

    契约：删除指定服务器配置并返回最新配置。
    失败语义：目标不存在返回 500。

    决策：复用 `update_server` 的删除分支
    问题：删除需与更新共享文件写入与缓存清理
    方案：调用 `update_server(delete=True)`
    代价：错误码与更新逻辑保持一致
    重评：若删除需要软删或回收站时调整
    """
    return await update_server(
        server_name,
        {},
        current_user,
        session,
        storage_service,
        settings_service,
        delete=True,
    )
