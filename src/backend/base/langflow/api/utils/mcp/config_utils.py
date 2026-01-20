"""
模块名称：`MCP` 配置与 `URL` 构建工具

本模块提供 `MCP` 服务器配置校验、`URL` 构建与启动时自动配置 `Starter Projects` 的能力。
主要功能包括：
- 校验项目 `MCP` 服务器名冲突与归属关系
- 生成 `Streamable HTTP` / `SSE` 连接地址（包含 `WSL` 本地回环适配）
- 读取并解密 `MCP Composer` 授权配置
- 启动时为每个用户配置 `Starter Projects` `MCP` 服务器与 `API Key`

关键组件：`MCPServerValidationResult` / `validate_mcp_server_for_project` /
`get_project_*_url` / `auto_configure_starter_projects_mcp`

设计背景：`MCP` 客户端需要稳定服务名与连接地址，`Starter Projects` 需开箱可用。
使用场景：项目创建/更新校验、服务启动时生成 `Starter Projects` 配置与连接地址。
注意事项：部分流程会创建 `API Key` 并写库；失败以日志记录为主，可能留下部分已更新状态。
"""

import asyncio
import platform
from asyncio.subprocess import create_subprocess_exec
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from lfx.base.mcp.constants import MAX_MCP_SERVER_NAME_LENGTH
from lfx.base.mcp.util import sanitize_mcp_name
from lfx.log import logger
from lfx.services.deps import get_settings_service
from sqlmodel import select

from langflow.api.v2.mcp import get_server_list, update_server
from langflow.services.auth.mcp_encryption import decrypt_auth_settings, encrypt_auth_settings
from langflow.services.database.models import Flow, Folder
from langflow.services.database.models.api_key.crud import create_api_key
from langflow.services.database.models.api_key.model import ApiKeyCreate
from langflow.services.database.models.folder.constants import DEFAULT_FOLDER_NAME
from langflow.services.database.models.user.model import User
from langflow.services.deps import get_storage_service

# 注意：`0.0.0.0` 仅用于绑定地址，客户端连接时必须替换为可达地址。
ALL_INTERFACES_HOST = "0.0.0.0"  # noqa: S104


class MCPServerValidationResult:
    """`MCP` 服务器校验结果载体。

    契约：
    - 字段：`server_exists`/`project_id_matches`/`server_name`/`existing_config`/`conflict_message`。
    - 用途：`validate_mcp_server_for_project` 返回后供调用方判断冲突、跳过或继续。
    - 失败语义：不抛异常；校验异常时通常返回 `server_exists=False` 以允许流程继续。

    关键路径：
    1) 构造结果对象。
    2) 通过 `has_conflict`/`should_skip`/`should_proceed` 决定后续流程。

    决策：以结果对象而非异常控制流程。
    问题：需要在冲突/缺失场景下返回可决策的上下文信息。
    方案：用布尔字段和 `conflict_message` 描述状态。
    代价：调用方需显式处理分支。
    重评：当状态维度扩展时改为枚举或异常体系。
    """

    def __init__(
        self,
        *,
        server_exists: bool,
        project_id_matches: bool,
        server_name: str = "",
        existing_config: dict | None = None,
        conflict_message: str = "",
    ):
        self.server_exists = server_exists
        self.project_id_matches = project_id_matches
        self.server_name = server_name
        self.existing_config = existing_config
        self.conflict_message = conflict_message

    @property
    def has_conflict(self) -> bool:
        """返回是否存在 `MCP` 同名冲突。

        契约：
        - 输入：无。
        - 输出：`True` 表示 `server_exists` 且 `project_id_matches` 为 `False`。

        关键路径：基于两项布尔字段计算冲突条件。

        决策：冲突以“存在且不匹配”定义。
        问题：需要统一冲突判定以减少调用方重复推理。
        方案：封装为只读属性。
        代价：判定条件变更需集中修改。
        重评：当冲突判定增加维度时改为枚举状态。
        """
        return self.server_exists and not self.project_id_matches

    @property
    def should_skip(self) -> bool:
        """返回是否可跳过配置。

        契约：
        - 输入：无。
        - 输出：`True` 表示 `server_exists` 且 `project_id_matches` 为 `True`。

        关键路径：判断服务器存在且归属当前项目。

        决策：已正确配置时直接跳过。
        问题：避免重复写入导致覆盖用户配置。
        方案：用只读属性暴露“可跳过”语义。
        代价：策略变更需同步更新属性逻辑。
        重评：当写入具备幂等保障时可考虑始终执行更新。
        """
        return self.server_exists and self.project_id_matches

    @property
    def should_proceed(self) -> bool:
        """返回是否可继续创建或更新。

        契约：
        - 输入：无。
        - 输出：`True` 表示不存在冲突（未存在或已归属）。

        关键路径：对 `server_exists` 与 `project_id_matches` 做合取判断。

        决策：冲突以外场景允许继续。
        问题：需要在创建/更新前快速给出“可继续”结论。
        方案：封装为可直接消费的布尔属性。
        代价：规则更新需集中维护。
        重评：当引入审批/锁机制时改为显式状态机。
        """
        return not self.server_exists or self.project_id_matches


async def validate_mcp_server_for_project(
    project_id: UUID,
    project_name: str,
    user,
    session,
    storage_service,
    settings_service,
    operation: str = "create",
) -> MCPServerValidationResult:
    """校验项目 `MCP` 服务器名是否冲突并返回决策信息。

    契约：
    - 输入：`project_id`/`project_name`/`user`/`session`/`storage_service`/`settings_service`；
      `operation` 取值为 `create`/`update`/`delete`。
    - 输出：`MCPServerValidationResult`，包含 `server_name` 与冲突描述。
    - 副作用：读取 `MCP` 配置与数据库；不写入。
    - 失败语义：校验异常时记录日志并返回 `server_exists=False`，允许后续流程继续。

    关键路径（三步）：
    1) 生成 `lf-` 前缀并截断到 `MAX_MCP_SERVER_NAME_LENGTH` 的服务器名。
    2) 拉取用户 `MCP` 配置并解析 `args` 中的 `SSE` 连接地址。
    3) 生成冲突信息并返回结果。

    异常流：`get_server_list` 失败或解析异常时记录日志并返回可继续结果。
    性能瓶颈：读取用户配置 + 正则扫描 `args` 列表。

    决策：从 `args` 中提取 `SSE` 地址匹配 `project_id`。
    问题：缺少可直接反查项目归属的后端接口。
    方案：解析 `URL` 列表并检查是否包含 `project_id` 字符串。
    代价：依赖 `URL` 结构与参数顺序，存在误判风险。
    重评：当配置结构化或新增查询接口时改为字段校验。

    排障入口：日志关键字 `validate MCP server`。
    """
    # 注意：名称会被截断以满足 `MCP` 服务器名长度上限。
    server_name = f"lf-{sanitize_mcp_name(project_name)[: (MAX_MCP_SERVER_NAME_LENGTH - 4)]}"

    try:
        existing_servers = await get_server_list(user, session, storage_service, settings_service)

        if server_name not in existing_servers.get("mcpServers", {}):
            return MCPServerValidationResult(
                project_id_matches=False,
                server_exists=False,
                server_name=server_name,
            )

        # 注意：同名服务器存在时需校验其归属项目。
        existing_server_config = existing_servers["mcpServers"][server_name]
        existing_args = existing_server_config.get("args", [])
        project_id_matches = False

        if existing_args:
            # 注意：当前假设 `SSE` 连接地址位于末尾，若参数顺序变更需同步调整。
            existing_sse_urls = await extract_urls_from_strings(existing_args)
            for existing_sse_url in existing_sse_urls:
                if str(project_id) in existing_sse_url:
                    project_id_matches = True
                    break
        else:
            project_id_matches = False

        conflict_message = ""
        if not project_id_matches:
            if operation == "create":
                conflict_message = (
                    f"MCP server name conflict: '{server_name}' already exists "
                    f"for a different project. Cannot create MCP server for project "
                    f"'{project_name}' (ID: {project_id})"
                )
            elif operation == "update":
                conflict_message = (
                    f"MCP server name conflict: '{server_name}' exists for a different project. "
                    f"Cannot update MCP server for project '{project_name}' (ID: {project_id})"
                )
            elif operation == "delete":
                conflict_message = (
                    f"MCP server '{server_name}' exists for a different project. "
                    f"Cannot delete MCP server for project '{project_name}' (ID: {project_id})"
                )

        return MCPServerValidationResult(
            server_exists=True,
            project_id_matches=project_id_matches,
            server_name=server_name,
            existing_config=existing_server_config,
            conflict_message=conflict_message,
        )

    except Exception as e:  # noqa: BLE001
        await logger.awarning(f"Could not validate MCP server for project {project_id}: {e}")
        # 注意：验证失败时放行，避免阻断项目创建/更新流程。
        return MCPServerValidationResult(
            project_id_matches=False,
            server_exists=False,
            server_name=server_name,
        )


async def get_url_by_os(host: str, port: int, url: str) -> str:
    """按运行环境调整连接地址（主要处理 `WSL` 本地回环）。

    契约：
    - 输入：`host`/`port`/`url`。
    - 输出：可被客户端访问的连接地址。
    - 副作用：在 `WSL` 场景下可能调用 `/usr/bin/hostname -I`。
    - 失败语义：获取 `WSL` `IP` 失败时记录警告并返回原始地址。

    关键路径（三步）：
    1) 识别 `WSL` 且 `host` 为 `localhost`/`127.0.0.1`。
    2) 调用 `/usr/bin/hostname -I` 获取可达 `IP`。
    3) 替换地址中的 `host` 并返回。

    异常流：子进程执行失败或 `OSError` 时记录警告并返回原始地址。
    性能瓶颈：每次调用都需拉起一次子进程获取 `IP`。

    决策：优先使用 `hostname -I` 获取 `WSL` 可达地址。
    问题：`WSL` 中 `localhost` 对外不可达，连接失败率高。
    方案：读取宿主可达 `IP` 并替换回环地址。
    代价：依赖系统命令与网络配置，可能取到不可用 `IP`。
    重评：当 `WSL` 提供稳定 `API` 或应用具备显式外部地址配置时改用配置项。

    排障入口：日志关键字 `Failed to get WSL IP address`。
    """
    os_type = platform.system()
    is_wsl = os_type == "Linux" and "microsoft" in platform.uname().release.lower()

    if is_wsl and host in {"localhost", "127.0.0.1"}:
        try:
            proc = await create_subprocess_exec(
                "/usr/bin/hostname",
                "-I",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()

            if proc.returncode == 0 and stdout.strip():
                # 注意：优先取首个 `IP` 作为可达地址。
                wsl_ip = stdout.decode().strip().split()[0]
                await logger.adebug("Using WSL IP for external access: %s", wsl_ip)
                # 注意：`WSL` 场景下 `localhost` 对外不可达，需要替换为宿主机可达 `IP`。
                url = url.replace(f"http://{host}:{port}", f"http://{wsl_ip}:{port}")
        except OSError as e:
            await logger.awarning("Failed to get WSL IP address: %s. Using default URL.", str(e))

    return url


async def _get_project_base_url_components() -> tuple[str, int]:
    """计算 `MCP` 项目的基础 `host`/`port`。

    契约：
    - 输入：无（读取 `settings`）。
    - 输出：`(host, port)`。
    - 副作用：读取 `settings` 服务配置。
    - 失败语义：缺失端口时回落到 `7860`，不抛异常。
    """
    # 注意：`runtime_port` 优先级高于配置端口，确保开发环境一致性。
    settings_service = get_settings_service()
    server_host = getattr(settings_service.settings, "host", "localhost")
    # 注意：优先使用运行时端口，缺失时回退到配置端口。
    server_port = (
        getattr(settings_service.settings, "runtime_port", None)
        or getattr(settings_service.settings, "port", None)
        or 7860
    )

    # 注意：`0.0.0.0` 仅是绑定地址，客户端必须使用可连接地址。
    host = "localhost" if server_host == ALL_INTERFACES_HOST else server_host
    return host, server_port


async def get_project_streamable_http_url(project_id: UUID) -> str:
    """生成项目的 `Streamable HTTP` 连接地址（不包含 `/sse`）。

    契约：
    - 输入：`project_id`。
    - 输出：对客户端可达的 `Streamable HTTP` 连接地址。
    - 副作用：无（仅读取配置并拼接连接地址）。
    - 失败语义：异常透传；`get_url_by_os` 内部已将常见 `OSError` 转为日志。

    决策：将项目连接地址固定到 `/api/v1/mcp/project/{id}/streamable`。
    问题：需要稳定的 `Streamable HTTP` 入口供客户端连接。
    方案：拼接固定路径并由 `get_url_by_os` 做本地回环适配。
    代价：路径变更需同步更新调用方。
    重评：当版本升级或路由迁移时改为配置化路径。
    """
    host, port = await _get_project_base_url_components()
    base_url = f"http://{host}:{port}".rstrip("/")
    project_url = f"{base_url}/api/v1/mcp/project/{project_id}/streamable"
    return await get_url_by_os(host, port, project_url)


async def get_project_sse_url(project_id: UUID) -> str:
    """生成项目的 `SSE` 连接地址（兼容 `WSL` 回环替换）。

    契约：
    - 输入：`project_id`。
    - 输出：对客户端可达的 `SSE` 连接地址。
    - 副作用：无（仅读取配置并拼接连接地址）。
    - 失败语义：异常透传；`get_url_by_os` 内部已将常见 `OSError` 转为日志。

    决策：保留 `/sse` 路由用于兼容旧客户端。
    问题：存量客户端依赖 `SSE` 连接地址。
    方案：继续拼接 `/api/v1/mcp/project/{id}/sse` 并统一适配 `WSL`。
    代价：双路径维护增加路由兼容成本。
    重评：当 `SSE` 客户端全部升级后移除该入口。
    """
    host, port = await _get_project_base_url_components()
    base_url = f"http://{host}:{port}".rstrip("/")
    project_sse_url = f"{base_url}/api/v1/mcp/project/{project_id}/sse"
    return await get_url_by_os(host, port, project_sse_url)


async def _get_mcp_composer_auth_config(project: Folder) -> dict:
    """读取并解密 `MCP Composer` 授权配置。

    契约：
    - 输入：`project`（使用 `project.auth_settings`）。
    - 输出：解密后的配置字典。
    - 副作用：解密 `auth_settings` 字段。
    - 失败语义：缺失或解密失败时抛 `ValueError`。
    """
    auth_config = None
    if project.auth_settings:
        decrypted_settings = decrypt_auth_settings(project.auth_settings)
        if decrypted_settings:
            auth_config = decrypted_settings

    if not auth_config:
        error_message = "Auth config is missing. Please check your settings and try again."
        raise ValueError(error_message)

    return auth_config


async def get_composer_streamable_http_url(project: Folder) -> str:
    """生成 `MCP Composer` 的 `Streamable HTTP` 连接地址。

    契约：
    - 输入：`project`（使用解密后的授权配置）。
    - 输出：`MCP Composer` 的 `Streamable HTTP` 连接地址。
    - 副作用：解密授权配置。
    - 失败语义：缺失 `oauth_host`/`oauth_port` 时抛 `ValueError`。

    决策：依赖 `oauth_host`/`oauth_port` 作为 `Composer` 入口。
    问题：`Composer` 部署地址不固定且需复用已有授权配置。
    方案：从解密配置中读取 `oauth_*` 并拼接连接地址。
    代价：配置缺失时直接失败，调用方需兜底。
    重评：当 `Composer` 提供服务发现或统一网关时改为动态解析。
    """
    auth_config = await _get_mcp_composer_auth_config(project)
    composer_host = auth_config.get("oauth_host")
    composer_port = auth_config.get("oauth_port")
    if not composer_host or not composer_port:
        error_msg = "OAuth host and port are required to get the MCP Composer URL"
        raise ValueError(error_msg)
    composer_url = f"http://{composer_host}:{composer_port}"
    return await get_url_by_os(composer_host, int(composer_port), composer_url)  # type: ignore[arg-type]


async def auto_configure_starter_projects_mcp(session):
    """启动时为每个用户配置 `Starter Projects` 的 `MCP` 服务器。

    契约：
    - 输入：`session` 用于读取/更新 `User`/`Folder`/`Flow` 并创建 `API Key`。
    - 输出：`None`；副作用包括更新 `Flow` 字段、生成 `API Key`、写入 `MCP` 配置并提交事务。
    - 失败语义：单用户异常记录日志并继续；整体失败仅记录错误，不抛出。

    关键路径（三步）：
    1) 校验 `add_projects_to_mcp_servers` 并定位用户的 `Starter Projects` 文件夹。
    2) 为 `Starter` 流程补齐 `mcp_enabled`/`action_*` 并保存。
    3) 生成 `uvx mcp-proxy` 配置与连接地址，写入 `MCP` 服务器配置。

    异常流：单用户处理失败记录错误并继续；提交失败时仅记录日志。
    性能瓶颈：全量用户扫描 + 每用户 `Flow` 遍历与配置写入。

    决策：启动时为 `Starter Projects` 统一补齐 `MCP` 服务器与凭据。
    问题：需要保证新用户开箱可用且无需手工配置。
    方案：扫描用户文件夹与 `Flow`，生成 `API Key` 并写入 `MCP` 配置。
    代价：启动路径会触发多次数据库写入与 `API Key` 生成。
    重评：当启动耗时或用户规模过大时改为异步批处理。

    排障入口：日志关键字 `starter projects MCP` / `AUTO_LOGIN` / `MCP server`.
    """
    # 注意：开关关闭时禁止写入，避免启动时批量改写用户配置。
    settings_service = get_settings_service()
    await logger.adebug("Starting auto-configure starter projects MCP")
    if not settings_service.settings.add_projects_to_mcp_servers:
        await logger.adebug("Auto-Configure MCP servers disabled, skipping starter project MCP configuration")
        return
    await logger.adebug(
        f"Auto-configure settings: add_projects_to_mcp_servers="
        f"{settings_service.settings.add_projects_to_mcp_servers}, "
        f"create_starter_projects={settings_service.settings.create_starter_projects}, "
        f"update_starter_projects={settings_service.settings.update_starter_projects}"
    )

    try:
        users = (await session.exec(select(User))).all()
        await logger.adebug(f"Found {len(users)} users in the system")
        if not users:
            await logger.adebug("No users found, skipping starter project MCP configuration")
            return

        total_servers_added = 0
        for user in users:
            await logger.adebug(f"Processing user: {user.username} (ID: {user.id})")
            try:
                # 注意：仅处理当前用户的文件夹，避免跨租户写入。
                all_user_folders = (await session.exec(select(Folder).where(Folder.user_id == user.id))).all()
                folder_names = [f.name for f in all_user_folders]
                await logger.adebug(f"User {user.username} has folders: {folder_names}")

                # 注意：`Starter Projects` 为每个用户单独的文件夹，`ID` 不共享。
                user_starter_folder = (
                    await session.exec(
                        select(Folder).where(
                            Folder.name == DEFAULT_FOLDER_NAME,
                            Folder.user_id == user.id,
                        )
                    )
                ).first()
                if not user_starter_folder:
                    await logger.adebug(
                        f"No starter projects folder ('{DEFAULT_FOLDER_NAME}') found for user {user.username}, skipping"
                    )
                    await logger.adebug(f"User {user.username} available folders: {folder_names}")
                    continue

                await logger.adebug(
                    f"Found starter folder '{user_starter_folder.name}' for {user.username}: "
                    f"ID={user_starter_folder.id}"
                )

                # 注意：只配置 `Starter Projects` 内的 `Flow`，避免污染用户其他项目。
                flows_query = select(Flow).where(
                    Flow.folder_id == user_starter_folder.id,
                    Flow.is_component == False,  # noqa: E712
                )
                user_starter_flows = (await session.exec(flows_query)).all()

                flows_configured = 0
                for flow in user_starter_flows:
                    if flow.mcp_enabled is None:
                        flow.mcp_enabled = True
                        if not flow.action_name:
                            flow.action_name = sanitize_mcp_name(flow.name)
                        if not flow.action_description:
                            flow.action_description = flow.description or f"Starter project: {flow.name}"
                        flow.updated_at = datetime.now(timezone.utc)
                        session.add(flow)
                        flows_configured += 1

                if flows_configured > 0:
                    await logger.adebug(f"Enabled MCP for {flows_configured} starter flows for user {user.username}")

                validation_result = await validate_mcp_server_for_project(
                    user_starter_folder.id,
                    DEFAULT_FOLDER_NAME,
                    user,
                    session,
                    get_storage_service(),
                    settings_service,
                    operation="create",
                )

                if validation_result.should_skip:
                    await logger.adebug(
                        f"MCP server '{validation_result.server_name}' already exists for user "
                        f"{user.username}'s starter projects (project ID: "
                        f"{user_starter_folder.id}), skipping"
                    )
                    continue

                server_name = validation_result.server_name

                # 注意：若未配置且非自动登录，默认强制 `API Key` 以避免匿名访问。
                default_auth = {"auth_type": "none"}
                await logger.adebug(f"Settings service auth settings: {settings_service.auth_settings}")
                await logger.adebug(f"User starter folder auth settings: {user_starter_folder.auth_settings}")
                if (
                    not user_starter_folder.auth_settings
                    and settings_service.auth_settings.AUTO_LOGIN
                    and not settings_service.auth_settings.SUPERUSER
                ):
                    default_auth = {"auth_type": "apikey"}
                    user_starter_folder.auth_settings = encrypt_auth_settings(default_auth)
                    await logger.adebug(
                        "AUTO_LOGIN enabled without SUPERUSER; forcing API key auth for starter folder %s",
                        user.username,
                    )
                elif not settings_service.auth_settings.AUTO_LOGIN and not user_starter_folder.auth_settings:
                    default_auth = {"auth_type": "apikey"}
                    user_starter_folder.auth_settings = encrypt_auth_settings(default_auth)
                    await logger.adebug(f"Set up auth settings for user {user.username}'s starter folder")
                elif user_starter_folder.auth_settings:
                    default_auth = user_starter_folder.auth_settings

                # 注意：`API Key` 为用户自身访问其 `Starter Projects` 的唯一凭据。
                api_key_name = f"MCP Project {DEFAULT_FOLDER_NAME} - {user.username}"
                unmasked_api_key = await create_api_key(session, ApiKeyCreate(name=api_key_name), user.id)

                streamable_http_url = await get_project_streamable_http_url(user_starter_folder.id)

                if default_auth.get("auth_type", "none") == "apikey":
                    command = "uvx"
                    args = [
                        "mcp-proxy",
                        "--transport",
                        "streamablehttp",
                        "--headers",
                        "x-api-key",
                        unmasked_api_key.api_key,
                        streamable_http_url,
                    ]
                elif default_auth.get("auth_type", "none") == "oauth":
                    msg = "OAuth authentication is not yet implemented for MCP server creation during project creation."
                    logger.warning(msg)
                    raise HTTPException(status_code=501, detail=msg)
                else:  # 注意：默认无鉴权。
                    # 注意：无鉴权模式仅用于可信环境，避免在公网暴露。
                    command = "uvx"
                    args = [
                        "mcp-proxy",
                        "--transport",
                        "streamablehttp",
                        streamable_http_url,
                    ]
                server_config = {"command": command, "args": args}

                await logger.adebug(f"Adding MCP server '{server_name}' for user {user.username}")
                await update_server(
                    server_name,
                    server_config,
                    user,
                    session,
                    get_storage_service(),
                    settings_service,
                )

                total_servers_added += 1
                await logger.adebug(f"Added starter projects MCP server for user: {user.username}")

            except Exception as e:  # noqa: BLE001
                # 注意：单用户失败不影响其他用户，避免启动流程被阻断。
                await logger.aerror(f"Could not add starter projects MCP server for user {user.username}: {e}")
                continue

        await session.commit()

        if total_servers_added > 0:
            await logger.adebug(f"Added starter projects MCP servers for {total_servers_added} users")
        else:
            await logger.adebug("No new starter project MCP servers were added")

    except Exception as e:  # noqa: BLE001
        await logger.aerror(f"Failed to auto-configure starter projects MCP servers: {e}")


async def extract_urls_from_strings(strings: list[str]) -> list[str]:
    """从字符串列表中提取 HTTP/HTTPS `URL`。

    契约：
    - 输入：字符串列表。
    - 输出：匹配到的 `URL` 列表（可为空）。
    - 副作用：无。
    - 失败语义：不抛异常；非字符串元素会被忽略。

    决策：使用正则一次性提取 `http/https` 链接。
    问题：`args` 中可能混杂参数与 URL，需要快速过滤。
    方案：匹配 `http`/`https` 前缀并排除常见闭合标点。
    代价：复杂 URL 或嵌套结构可能漏检。
    重评：当需高精度解析时改用 URL 解析库。
    """
    import re

    # 注意：排除常见闭合标点，避免把尾随符号视为 `URL` 的一部分。
    url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+[^\s<>"{}|\\^`\[\].,;:!?]'

    urls = []
    for string in strings:
        if isinstance(string, str):
            found_urls = re.findall(url_pattern, string)
            urls.extend(found_urls)

    return urls
