import asyncio
import json
import os
import re
import tempfile
import warnings
from contextlib import asynccontextmanager
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib.parse import urlencode

import anyio
import httpx
import sqlalchemy
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi_pagination import add_pagination
from filelock import FileLock
from lfx.interface.utils import setup_llm_caching
from lfx.log.logger import configure, logger
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from pydantic import PydanticDeprecatedSince20
from pydantic_core import PydanticSerializationError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from langflow.api import health_check_router, log_router, router
from langflow.api.v1.mcp_projects import init_mcp_servers
from langflow.initial_setup.setup import (
    copy_profile_pictures,
    create_or_update_starter_projects,
    initialize_auto_login_default_superuser,
    load_bundles_from_urls,
    load_flows_from_directory,
    sync_flows_from_fs,
)
from langflow.middleware import ContentSizeLimitMiddleware
from langflow.services.deps import (
    get_queue_service,
    get_service,
    get_settings_service,
    get_telemetry_service,
    session_scope,
)
from langflow.services.schema import ServiceType
from langflow.services.utils import initialize_services, initialize_settings_service, teardown_services
from langflow.utils.mcp_cleanup import cleanup_mcp_sessions

if TYPE_CHECKING:
    from tempfile import TemporaryDirectory

    from lfx.services.mcp_composer.service import MCPComposerService

# 忽略来自Langchain的Pydantic弃用警告
warnings.filterwarnings("ignore", category=PydanticDeprecatedSince20)

# 抑制来自anyio流的ResourceWarning（SSE连接）
warnings.filterwarnings("ignore", category=ResourceWarning, message=".*MemoryObjectReceiveStream.*")
warnings.filterwarnings("ignore", category=ResourceWarning, message=".*MemoryObjectSendStream.*")

_tasks: list[asyncio.Task] = []

MAX_PORT = 65535


async def log_exception_to_telemetry(exc: Exception, context: str) -> None:
    """将异常安全地记录到遥测系统而不引发
    
    决策：使用try-catch包装遥测记录调用
    问题：遥测服务本身可能抛出异常，导致原异常被掩盖
    方案：捕获遥测服务的异常并仅记录警告，确保原始异常得到处理
    代价：可能丢失一些遥测数据，但保证原始异常处理不受影响
    重评：当遥测服务稳定性提升时可以重新评估异常处理策略
    
    关键路径（三步）：
    1) 获取遥测服务实例
    2) 尝试记录异常
    3) 捕获并记录遥测调用本身的异常
    
    异常流：如果遥测服务抛出httpx.HTTPError或asyncio.QueueFull异常，则记录警告
    性能瓶颈：无显著性能瓶颈
    排障入口：遥测异常记录的警告信息
    """
    try:
        telemetry_service = get_telemetry_service()
        await telemetry_service.log_exception(exc, context)
    except (httpx.HTTPError, asyncio.QueueFull):
        await logger.awarning(f"Failed to log {context} exception to telemetry")


class RequestCancelledMiddleware(BaseHTTPMiddleware):
    """请求取消中间件，用于检测和处理客户端断开连接的情况"""
    
    def __init__(self, app) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """处理请求调度，检测客户端断开连接
        
        关键路径（三步）：
        1) 启动取消处理器任务以监控请求断开
        2) 启动请求处理任务
        3) 等待两个任务并根据结果返回响应
        
        异常流：如果检测到请求断开则返回499状态码
        性能瓶颈：无显著性能瓶颈
        排障入口：无特定日志关键字
        """
        sentinel = object()

        async def cancel_handler():
            """监控请求断开的异步处理器"""
            while True:
                if await request.is_disconnected():
                    return sentinel
                await asyncio.sleep(0.1)

        handler_task = asyncio.create_task(call_next(request))
        cancel_task = asyncio.create_task(cancel_handler())

        done, pending = await asyncio.wait([handler_task, cancel_task], return_when=asyncio.FIRST_COMPLETED)

        for task in pending:
            task.cancel()

        if cancel_task in done:
            return Response("Request was cancelled", status_code=499)
        return await handler_task


class JavaScriptMIMETypeMiddleware(BaseHTTPMiddleware):
    """JavaScript MIME类型中间件，确保JS文件返回正确的Content-Type头"""
    
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """处理请求调度，确保JS文件返回正确的MIME类型
        
        关键路径（三步）：
        1) 调用下一个处理器获取响应
        2) 检查是否为JS文件请求
        3) 如果是JS文件且状态正常，则设置正确的Content-Type
        
        异常流：如果遇到Pydantic序列化错误则抛出HTTP异常
        性能瓶颈：无显著性能瓶颈
        排障入口：无特定日志关键字
        """
        try:
            response = await call_next(request)
        except Exception as exc:
            if isinstance(exc, PydanticSerializationError):
                message = (
                    "Something went wrong while serializing the response. "
                    "Please share this error on our GitHub repository."
                )
                error_messages = json.dumps([message, str(exc)])
                raise HTTPException(status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail=error_messages) from exc
            raise
        if (
            "files/" not in request.url.path
            and request.url.path.endswith(".js")
            and response.status_code == HTTPStatus.OK
        ):
            response.headers["Content-Type"] = "text/javascript"
        return response


async def load_bundles_with_error_handling():
    """带错误处理的捆绑包加载函数
    
    关键路径（三步）：
    1) 尝试加载捆绑包
    2) 捕获HTTP相关异常
    3) 返回空列表作为降级
    
    异常流：捕获httpx.TimeoutException、HTTPError、RequestError并记录错误
    性能瓶颈：无显著性能瓶颈
    排障入口：错误日志记录
    """
    try:
        return await load_bundles_from_urls()
    except (httpx.TimeoutException, httpx.HTTPError, httpx.RequestError) as exc:
        await logger.aerror(f"Error loading bundles from URLs: {exc}")
        return [], []


def warn_about_future_cors_changes(settings):
    """警告用户关于未来CORS安全更改，将在版本1.7中生效"""
    # 检查是否使用默认（向后兼容）设置
    using_defaults = settings.cors_origins == "*" and settings.cors_allow_credentials is True

    if using_defaults:
        logger.warning(
            "CORS: Using permissive defaults (all origins + credentials). "
            "Set LANGFLOW_CORS_ORIGINS for production. Stricter defaults in v2.0."
        )


def get_lifespan(*, fix_migration=False, version=None):
    """获取应用生命周期管理器
    
    决策：使用asynccontextmanager管理应用生命周期
    问题：需要在启动时初始化多个服务，在关闭时正确清理资源
    方案：使用FastAPI的lifespan机制统一管理启动和关闭逻辑
    代价：增加了代码复杂性，但提供了更好的资源管理
    重评：当服务依赖关系发生变化时需要重新评估
    
    关键路径（三步）：
    1) 初始化设置和服务
    2) 启动时执行各项初始化任务
    3) 关闭时执行清理任务
    
    异常流：捕获并记录生命周期中的异常，通过遥测系统报告
    性能瓶颈：启动时的初始化任务可能耗时较长
    排障入口：启动时间日志、异常日志
    """
    initialize_settings_service()
    telemetry_service = get_telemetry_service()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        from lfx.interface.components import get_and_cache_all_types_dict

        configure()

        # 启动消息
        if version:
            await logger.adebug(f"Starting Langflow v{version}...")
        else:
            await logger.adebug("Starting Langflow...")

        temp_dirs: list[TemporaryDirectory] = []
        sync_flows_from_fs_task = None
        mcp_init_task = None

        try:
            start_time = asyncio.get_event_loop().time()

            await logger.adebug("Initializing services")
            await initialize_services(fix_migration=fix_migration)
            await logger.adebug(f"Services initialized in {asyncio.get_event_loop().time() - start_time:.2f}s")

            current_time = asyncio.get_event_loop().time()
            await logger.adebug("Setting up LLM caching")
            setup_llm_caching()
            await logger.adebug(f"LLM caching setup in {asyncio.get_event_loop().time() - current_time:.2f}s")

            current_time = asyncio.get_event_loop().time()
            await logger.adebug("Copying profile pictures")
            await copy_profile_pictures()
            await logger.adebug(f"Profile pictures copied in {asyncio.get_event_loop().time() - current_time:.2f}s")

            if get_settings_service().auth_settings.AUTO_LOGIN:
                current_time = asyncio.get_event_loop().time()
                await logger.adebug("Initializing default super user")
                await initialize_auto_login_default_superuser()
                await logger.adebug(
                    f"Default super user initialized in {asyncio.get_event_loop().time() - current_time:.2f}s"
                )

            await logger.adebug("Initializing super user")
            await initialize_auto_login_default_superuser()
            await logger.adebug(f"Super user initialized in {asyncio.get_event_loop().time() - current_time:.2f}s")

            current_time = asyncio.get_event_loop().time()
            await logger.adebug("Loading bundles")
            temp_dirs, bundles_components_paths = await load_bundles_with_error_handling()
            get_settings_service().settings.components_path.extend(bundles_components_paths)
            await logger.adebug(f"Bundles loaded in {asyncio.get_event_loop().time() - current_time:.2f}s")

            current_time = asyncio.get_event_loop().time()
            await logger.adebug("Caching types")
            all_types_dict = await get_and_cache_all_types_dict(get_settings_service(), telemetry_service)
            await logger.adebug(f"Types cached in {asyncio.get_event_loop().time() - current_time:.2f}s")

            # 使用基于文件的锁以防止多个工作进程同时创建重复的入门项目
            # 注意，仍有可能一个工作进程完成此任务，释放锁，
            # 然后另一个工作进程接手它，但由于操作是幂等的，最坏情况是复制
            # 初始化工作
            current_time = asyncio.get_event_loop().time()
            await logger.adebug("Creating/updating starter projects")

            lock_file = Path(tempfile.gettempdir()) / "langflow_starter_projects.lock"
            lock = FileLock(lock_file, timeout=1)
            try:
                with lock:
                    await create_or_update_starter_projects(all_types_dict)
                    await logger.adebug(
                        f"Starter projects created/updated in {asyncio.get_event_loop().time() - current_time:.2f}s"
                    )
            except TimeoutError:
                # 另一个进程拥有锁
                await logger.adebug("Another worker is creating starter projects, skipping")
            except Exception as e:  # noqa: BLE001
                await logger.awarning(
                    f"Failed to acquire lock for starter projects: {e}. Starter projects may not be created or updated."
                )

            # 早期初始化代理全局变量（在MCP服务器和流之前）
            if get_settings_service().settings.agentic_experience:
                from langflow.api.utils.mcp.agentic_mcp import initialize_agentic_global_variables

                current_time = asyncio.get_event_loop().time()
                await logger.ainfo("Initializing agentic global variables...")
                try:
                    async with session_scope() as session:
                        await initialize_agentic_global_variables(session)
                    await logger.adebug(
                        f"Agentic global variables initialized in {asyncio.get_event_loop().time() - current_time:.2f}s"
                    )
                except Exception as e:  # noqa: BLE001
                    await logger.awarning(f"Failed to initialize agentic global variables: {e}")

            current_time = asyncio.get_event_loop().time()
            await logger.adebug("Starting telemetry service")
            telemetry_service.start()
            await logger.adebug(f"started telemetry service in {asyncio.get_event_loop().time() - current_time:.2f}s")

            current_time = asyncio.get_event_loop().time()
            await logger.adebug("Starting MCP Composer service")
            mcp_composer_service = cast("MCPComposerService", get_service(ServiceType.MCP_COMPOSER_SERVICE))
            await mcp_composer_service.start()
            await logger.adebug(
                f"started MCP Composer service in {asyncio.get_event_loop().time() - current_time:.2f}s"
            )

            # 如果启用则自动配置代理MCP服务器（在变量初始化后）
            if get_settings_service().settings.agentic_experience:
                from langflow.api.utils.mcp.agentic_mcp import auto_configure_agentic_mcp_server

                current_time = asyncio.get_event_loop().time()
                await logger.ainfo("Configuring Agentic MCP server...")
                try:
                    async with session_scope() as session:
                        await auto_configure_agentic_mcp_server(session)
                    await logger.adebug(
                        f"Agentic MCP server configured in {asyncio.get_event_loop().time() - current_time:.2f}s"
                    )
                except Exception as e:  # noqa: BLE001
                    await logger.awarning(f"Failed to configure agentic MCP server: {e}")

            current_time = asyncio.get_event_loop().time()
            await logger.adebug("Loading flows")
            await load_flows_from_directory()
            sync_flows_from_fs_task = asyncio.create_task(sync_flows_from_fs())
            queue_service = get_queue_service()
            if not queue_service.is_started():  # 如果尚未启动则启动
                queue_service.start()
            await logger.adebug(f"Flows loaded in {asyncio.get_event_loop().time() - current_time:.2f}s")

            total_time = asyncio.get_event_loop().time() - start_time
            await logger.adebug(f"Total initialization time: {total_time:.2f}s")

            async def delayed_init_mcp_servers():
                """延迟初始化MCP服务器，避免与入门项目创建发生竞争条件"""
                await asyncio.sleep(10.0)  # 增加延迟以允许入门项目被创建
                current_time = asyncio.get_event_loop().time()
                await logger.adebug("Loading MCP servers for projects")
                try:
                    await init_mcp_servers()
                    await logger.adebug(f"MCP servers loaded in {asyncio.get_event_loop().time() - current_time:.2f}s")
                except Exception as e:  # noqa: BLE001
                    await logger.awarning(f"First MCP server initialization attempt failed: {e}")
                    await asyncio.sleep(5.0)  # 增加重试延迟
                    current_time = asyncio.get_event_loop().time()
                    await logger.adebug("Retrying MCP servers initialization")
                    try:
                        await init_mcp_servers()
                        await logger.adebug(
                            f"MCP servers loaded on retry in {asyncio.get_event_loop().time() - current_time:.2f}s"
                        )
                    except Exception as e2:  # noqa: BLE001
                        await logger.aexception(f"Failed to initialize MCP servers after retry: {e2}")

            # 将延迟初始化作为后台任务启动
            # 允许服务器首先启动以避免与MCP服务器启动的竞争条件
            mcp_init_task = asyncio.create_task(delayed_init_mcp_servers())

            # v1和项目MCP服务器上下文管理器
            from langflow.api.v1.mcp import start_streamable_http_manager
            from langflow.api.v1.mcp_projects import start_project_task_group

            await start_streamable_http_manager()
            await start_project_task_group()

            yield
        except asyncio.CancelledError:
            await logger.adebug("Lifespan received cancellation signal")
        except Exception as exc:
            if "langflow migration --fix" not in str(exc):
                logger.exception(exc)

                await log_exception_to_telemetry(exc, "lifespan")
            raise
        finally:
            # 关键：首先清理MCP会话，在任何其他关闭逻辑之前。
            # 这确保即使关闭被中断，MCP子进程也会被终止。
            await cleanup_mcp_sessions()

            # 清理关闭，带有进度指示器
            # 创建关闭进度（如果日志级别为DEBUG则显示详细时间）
            from langflow.__main__ import get_number_of_workers
            from langflow.cli.progress import create_langflow_shutdown_progress

            log_level = os.getenv("LANGFLOW_LOG_LEVEL", "info").lower()
            num_workers = get_number_of_workers(get_settings_service().settings.workers)
            shutdown_progress = create_langflow_shutdown_progress(
                verbose=log_level == "debug", multiple_workers=num_workers > 1
            )

            try:
                # 步骤0：停止服务器
                with shutdown_progress.step(0):
                    await logger.adebug("Stopping server gracefully...")
                    # 实际的服务器停止由lifespan上下文处理
                    await asyncio.sleep(0.1)  # 短暂暂停以产生视觉效果

                # 步骤1：取消后台任务
                with shutdown_progress.step(1):
                    from langflow.api.v1.mcp import stop_streamable_http_manager
                    from langflow.api.v1.mcp_projects import stop_project_task_group

                    # 关闭MCP项目服务器
                    try:
                        await stop_project_task_group()
                    except Exception as e:  # noqa: BLE001
                        await logger.aerror(f"Failed to stop MCP Project servers: {e}")
                    # 关闭MCP服务器streamable-http会话管理器.run()上下文管理器
                    try:
                        await stop_streamable_http_manager()
                    except Exception as e:  # noqa: BLE001
                        await logger.aerror(f"Failed to stop MCP server streamable-http session manager: {e}")
                    # 取消后台任务
                    tasks_to_cancel = []
                    if sync_flows_from_fs_task:
                        sync_flows_from_fs_task.cancel()
                        tasks_to_cancel.append(sync_flows_from_fs_task)
                    if mcp_init_task and not mcp_init_task.done():
                        mcp_init_task.cancel()
                        tasks_to_cancel.append(mcp_init_task)
                    if tasks_to_cancel:
                        # 等待所有任务完成，捕获异常
                        results = await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
                        # 记录任何非取消异常
                        for result in results:
                            if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
                                await logger.aerror(f"Error during task cleanup: {result}", exc_info=result)

                # 步骤2：清理服务
                with shutdown_progress.step(2):
                    try:
                        await asyncio.wait_for(teardown_services(), timeout=30)
                    except asyncio.TimeoutError:
                        await logger.awarning("Teardown services timed out after 30s.")

                # 步骤3：清除临时文件
                with shutdown_progress.step(3):
                    temp_dir_cleanups = [asyncio.to_thread(temp_dir.cleanup) for temp_dir in temp_dirs]
                    try:
                        await asyncio.wait_for(asyncio.gather(*temp_dir_cleanups), timeout=10)
                    except asyncio.TimeoutError:
                        await logger.awarning("Temporary file cleanup timed out after 10s.")

                # 步骤4：完成关闭
                with shutdown_progress.step(4):
                    await logger.adebug("Langflow shutdown complete")

                # 显示完成摘要和告别
                shutdown_progress.print_shutdown_summary()

            except (sqlalchemy.exc.OperationalError, sqlalchemy.exc.DBAPIError) as e:
                # 数据库连接在关闭过程中关闭的情况
                await logger.awarning(f"Database teardown failed due to closed connection: {e}")
            except asyncio.CancelledError:
                # 吞下这个 - 在关闭期间是正常的
                await logger.adebug("Teardown cancelled during shutdown.")
            except Exception as e:  # noqa: BLE001
                await logger.aexception(f"Unhandled error during cleanup: {e}")
                await log_exception_to_telemetry(e, "lifespan_cleanup")

    return lifespan


def create_app():
    """创建FastAPI应用并包含路由器
    
    决策：使用FastAPI框架构建Web API
    问题：需要配置多种中间件、路由、异常处理和遥测
    方案：集中创建应用实例并配置所有必需组件
    代价：函数变得相当庞大，但保持了配置逻辑的集中性
    重评：当应用架构发生变化时需要重新评估
    
    关键路径（三步）：
    1) 初始化FastAPI应用和生命周期管理
    2) 配置各种中间件和CORS设置
    3) 添加路由、异常处理器和遥测
    
    异常流：配置异常会被记录并通过HTTP响应返回
    性能瓶颈：启动时的初始化可能耗时较长
    排障入口：启动日志、异常日志
    """
    from langflow.utils.version import get_version_info

    __version__ = get_version_info()["version"]
    configure()
    lifespan = get_lifespan(version=__version__)
    app = FastAPI(
        title="Langflow",
        version=__version__,
        lifespan=lifespan,
    )
    app.add_middleware(
        ContentSizeLimitMiddleware,
    )

    setup_sentry(app)

    settings = get_settings_service().settings

    # 警告关于未来的CORS更改
    warn_about_future_cors_changes(settings)

    # 使用设置配置CORS（向后兼容的默认值）
    origins = settings.cors_origins
    if isinstance(origins, str) and origins != "*":
        origins = [origins]

    # 应用当前CORS配置（保持向后兼容性）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=settings.cors_allow_methods,
        allow_headers=settings.cors_allow_headers,
    )
    app.add_middleware(JavaScriptMIMETypeMiddleware)

    @app.middleware("http")
    async def check_boundary(request: Request, call_next):
        """检查multipart/form-data边界参数的有效性
        
        关键路径（三步）：
        1) 验证Content-Type头部包含有效的边界参数
        2) 验证边界格式符合规范
        3) 验证multipart格式正确性
        
        异常流：不符合要求时返回422状态码
        性能瓶颈：无显著性能瓶颈
        排障入口：无特定日志关键字
        """
        if "/api/v1/files/upload" in request.url.path:
            content_type = request.headers.get("Content-Type")

            if not content_type or "multipart/form-data" not in content_type or "boundary=" not in content_type:
                return JSONResponse(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    content={"detail": "Content-Type header must be 'multipart/form-data' with a boundary parameter."},
                )

            boundary = content_type.split("boundary=")[-1].strip()

            if not re.match(r"^[\w\-]{1,70}$", boundary):
                return JSONResponse(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    content={"detail": "Invalid boundary format"},
                )

            body = await request.body()

            boundary_start = f"--{boundary}".encode()
            # multipart/form-data规范不要求边界后有换行，但许多客户端
            # 以这种方式实现
            boundary_end = f"--{boundary}--\r\n".encode()
            boundary_end_no_newline = f"--{boundary}--".encode()

            if not body.startswith(boundary_start) or not body.endswith((boundary_end, boundary_end_no_newline)):
                return JSONResponse(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    content={"detail": "Invalid multipart formatting"},
                )

        return await call_next(request)

    @app.middleware("http")
    async def flatten_query_string_lists(request: Request, call_next):
        """扁平化查询字符串列表，将逗号分隔的值拆分为单独的参数
        
        关键路径（三步）：
        1) 遍历查询参数的多值项
        2) 将逗号分隔的值拆分为独立条目
        3) 重新编码查询字符串
        
        异常流：无特殊异常处理
        性能瓶颈：无显著性能瓶颈
        排障入口：无特定日志关键字
        """
        flattened: list[tuple[str, str]] = []
        for key, value in request.query_params.multi_items():
            flattened.extend((key, entry) for entry in value.split(","))

        request.scope["query_string"] = urlencode(flattened, doseq=True).encode("utf-8")

        return await call_next(request)

    if prome_port_str := os.environ.get("LANGFLOW_PROMETHEUS_PORT"):
        # 为create_app()入口点设置
        prome_port = int(prome_port_str)
        if prome_port > 0 or prome_port < MAX_PORT:
            logger.debug(f"Starting Prometheus server on port {prome_port}...")
            settings.prometheus_enabled = True
            settings.prometheus_port = prome_port
        else:
            msg = f"Invalid port number {prome_port_str}"
            raise ValueError(msg)

    if settings.prometheus_enabled:
        from prometheus_client import start_http_server

        start_http_server(settings.prometheus_port)

    if settings.mcp_server_enabled:
        from langflow.api.v1 import mcp_router

        router.include_router(mcp_router)

    app.include_router(router)
    app.include_router(health_check_router)
    app.include_router(log_router)

    @app.exception_handler(Exception)
    async def exception_handler(_request: Request, exc: Exception):
        """全局异常处理器
        
        关键路径（三步）：
        1) 检查异常是否为HTTPException
        2) 记录异常信息
        3) 返回适当的JSON响应
        
        异常流：所有未处理的异常都会经过此处理器
        性能瓶颈：无显著性能瓶颈
        排障入口：异常日志记录
        """
        if isinstance(exc, HTTPException):
            await logger.aerror(f"HTTPException: {exc}", exc_info=exc)
            return JSONResponse(
                status_code=exc.status_code,
                content={"message": str(exc.detail)},
            )
        await logger.aerror(f"unhandled error: {exc}", exc_info=exc)

        await log_exception_to_telemetry(exc, "handler")

        return JSONResponse(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            content={"message": str(exc)},
        )

    FastAPIInstrumentor.instrument_app(app)

    add_pagination(app)

    return app


def setup_sentry(app: FastAPI) -> None:
    """配置Sentry错误跟踪
    
    关键路径（三步）：
    1) 检查是否配置了Sentry DSN
    2) 初始化Sentry SDK
    3) 添加Sentry ASGI中间件
    
    异常流：无特殊异常处理
    性能瓶颈：无显著性能瓶颈
    排障入口：Sentry错误报告
    """
    settings = get_settings_service().settings
    if settings.sentry_dsn:
        import sentry_sdk
        from sentry_sdk.integrations.asgi import SentryAsgiMiddleware

        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            traces_sample_rate=settings.sentry_traces_sample_rate,
            profiles_sample_rate=settings.sentry_profiles_sample_rate,
        )
        app.add_middleware(SentryAsgiMiddleware)


def setup_static_files(app: FastAPI, static_files_dir: Path) -> None:
    """设置静态文件目录
    
    决策：使用FastAPI的StaticFiles功能提供前端资源
    问题：需要将静态文件（如index.html）正确挂载到根路径
    方案：使用StaticFiles中间件并将404错误重定向到index.html
    代价：可能导致某些错误的路由行为，但适用于SPA
    重评：当需要更复杂的前端路由时需要重新评估
    
    关键路径（三步）：
    1) 挂载静态文件目录到根路径
    2) 配置404错误处理器返回index.html
    3) 验证index.html文件存在
    
    异常流：如果index.html不存在则抛出运行时错误
    性能瓶颈：无显著性能瓶颈
    排障入口：文件存在性检查的错误消息
    """
    app.mount(
        "/",
        StaticFiles(directory=static_files_dir, html=True),
        name="static",
    )

    @app.exception_handler(404)
    async def custom_404_handler(_request, _exc):
        path = anyio.Path(static_files_dir) / "index.html"

        if not await path.exists():
            msg = f"File at path {path} does not exist."
            raise RuntimeError(msg)
        return FileResponse(path)


def get_static_files_dir():
    """获取相对于Langflow main.py文件的静态文件目录"""
    frontend_path = Path(__file__).parent
    return frontend_path / "frontend"


def setup_app(static_files_dir: Path | None = None, *, backend_only: bool = False) -> FastAPI:
    """设置FastAPI应用
    
    决策：提供灵活的应用配置选项
    问题：需要支持仅后端模式和前端+后端模式
    方案：使用backend_only参数控制是否挂载静态文件
    代价：增加了函数复杂性，但提供了更大的灵活性
    重评：当部署需求变化时需要重新评估
    
    关键路径（三步）：
    1) 确定静态文件目录位置
    2) 创建基本应用实例
    3) 根据模式决定是否添加静态文件支持
    
    异常流：如果backend_only为False且静态文件目录不存在则抛出RuntimeError
    性能瓶颈：无显著性能瓶颈
    排障入口：静态文件目录存在性检查
    """
    # 获取当前文件的目录
    if not static_files_dir:
        static_files_dir = get_static_files_dir()

    if not backend_only and (not static_files_dir or not static_files_dir.exists()):
        msg = f"Static files directory {static_files_dir} does not exist."
        raise RuntimeError(msg)
    app = create_app()

    if not backend_only and static_files_dir is not None:
        setup_static_files(app, static_files_dir)
    return app


if __name__ == "__main__":
    import uvicorn

    from langflow.__main__ import get_number_of_workers

    configure()
    uvicorn.run(
        "langflow.main:create_app",
        host="localhost",
        port=7860,
        workers=get_number_of_workers(),
        log_level="error",
        reload=True,
        loop="asyncio",
    )
