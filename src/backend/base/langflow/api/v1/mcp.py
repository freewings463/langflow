"""
模块名称：MCP 全局服务接口

本模块提供全局 MCP Server 的 SSE 与 Streamable HTTP 传输入口，并封装工具调用。
主要功能：
- 提供 MCP 资源/工具列表与调用入口
- 建立 SSE 与 Streamable HTTP 传输通道
- 管理会话生命周期与错误处理
设计背景：统一 MCP 服务暴露与传输层实现。
注意事项：部分传输在 Astra Cloud 环境中被禁用。
"""

import asyncio

import pydantic
from anyio import BrokenResourceError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from lfx.log.logger import logger
from mcp import types
from mcp.server import NotificationOptions, Server
from mcp.server.sse import SseServerTransport
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from langflow.api.utils import CurrentActiveMCPUser, raise_error_if_astra_cloud_env
from langflow.api.v1.mcp_utils import (
    current_user_ctx,
    handle_call_tool,
    handle_list_resources,
    handle_list_tools,
    handle_mcp_errors,
    handle_read_resource,
)

router = APIRouter(prefix="/mcp", tags=["mcp"])

server = Server("langflow-mcp-server")


@server.list_prompts()
async def handle_list_prompts():
    """返回 MCP Prompt 列表（当前为空）。"""
    return []


@server.list_resources()
async def handle_global_resources():
    """获取全局 MCP 资源列表。"""
    return await handle_list_resources()


@server.read_resource()
async def handle_global_read_resource(uri: str) -> bytes:
    """读取全局 MCP 资源内容。"""
    return await handle_read_resource(uri)


@server.list_tools()
async def handle_global_tools():
    """获取全局 MCP 工具列表。"""
    return await handle_list_tools()


@server.call_tool()
@handle_mcp_errors
async def handle_global_call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    """执行全局 MCP 工具调用。"""
    return await handle_call_tool(name, arguments, server)


# 设计说明：传输层已接管 ASGI 响应，但 FastAPI 仍要求返回 `Response`，
# 而 Starlette 在重复 `http.response.start` 时会报错。
# `ResponseNoOp` 用于吞掉冗余响应，确保流式连接正常结束。
class ResponseNoOp(Response):
    """吞掉重复响应头的空响应，用于 SSE 兼容。"""

    async def __call__(self, scope, receive, send) -> None:  # noqa: ARG002
        return


def find_validation_error(exc):
    """从异常链中查找 `pydantic.ValidationError`。"""
    while exc:
        if isinstance(exc, pydantic.ValidationError):
            return exc
        exc = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
    return None


################################################################################
# SSE Transport
################################################################################
sse = SseServerTransport("/api/v1/mcp/")


@router.head(
    "/sse",
    response_class=HTMLResponse,
    include_in_schema=False,
    dependencies=[Depends(raise_error_if_astra_cloud_env)],
)
async def im_alive():
    """SSE 存活探测。"""
    return Response()


@router.get(
    "/sse",
    response_class=ResponseNoOp,
    dependencies=[Depends(raise_error_if_astra_cloud_env)],
)
async def handle_sse(request: Request, current_user: CurrentActiveMCPUser):
    """建立 MCP SSE 连接并运行事件循环。"""
    msg = f"Starting SSE connection, server name: {server.name}"
    await logger.ainfo(msg)
    token = current_user_ctx.set(current_user)
    try:
        async with sse.connect_sse(request.scope, request.receive, request._send) as streams:  # noqa: SLF001
            try:
                msg = "Starting SSE connection"
                await logger.adebug(msg)
                msg = f"Stream types: read={type(streams[0])}, write={type(streams[1])}"
                await logger.adebug(msg)

                notification_options = NotificationOptions(
                    prompts_changed=True, resources_changed=True, tools_changed=True
                )
                init_options = server.create_initialization_options(notification_options)
                msg = f"Initialization options: {init_options}"
                await logger.adebug(msg)

                try:
                    await server.run(streams[0], streams[1], init_options)
                except Exception as exc:  # noqa: BLE001
                    validation_error = find_validation_error(exc)
                    if validation_error:
                        msg = "Validation error in MCP:" + str(validation_error)
                        await logger.adebug(msg)
                    else:
                        msg = f"Error in MCP: {exc!s}"
                        await logger.adebug(msg)
                        return
            except BrokenResourceError:
                # Handle gracefully when client disconnects
                await logger.ainfo("Client disconnected from SSE connection")
            except asyncio.CancelledError:
                await logger.ainfo("SSE connection was cancelled")
                raise
            except Exception as e:
                msg = f"Error in MCP: {e!s}"
                await logger.aexception(msg)
                raise
    finally:
        current_user_ctx.reset(token)


@router.post("/", dependencies=[Depends(raise_error_if_astra_cloud_env)])
async def handle_messages(request: Request):
    """处理 MCP POST 消息入口。"""
    try:
        await sse.handle_post_message(request.scope, request.receive, request._send)  # noqa: SLF001
    except (BrokenResourceError, BrokenPipeError) as e:
        await logger.ainfo("MCP Server disconnected")
        raise HTTPException(status_code=404, detail=f"MCP Server disconnected, error: {e}") from e
    except Exception as e:
        await logger.aerror(f"Internal server error: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}") from e


################################################################################
# Streamable HTTP Transport
################################################################################
class StreamableHTTP:
    """Streamable HTTP 传输会话管理器。"""

    def __init__(self):
        self.session_manager: StreamableHTTPSessionManager | None = None
        self._started = False
        self._start_stop_lock = asyncio.Lock()
        # own the lifecycle of the session manager
        # inside an asyncio task to ensure that
        # __aenter__ and __aexit__ happen in the same task
        self._mgr_task: asyncio.Task | None = None
        self._mgr_ready: asyncio.Event | None = None
        self._mgr_close: asyncio.Event | None = None

    async def _start_session_manager(self) -> None:
        """启动 Streamable HTTP 会话管理器生命周期。"""
        try:
            async with self.session_manager.run():  # type: ignore[union-attr]
                self._started = True
                self._mgr_ready.set()  # type: ignore[union-attr]
                await self._mgr_close.wait()  # type: ignore[union-attr]
        except Exception as e:
            msg = f"Error in Streamable HTTP session manager: {e}"
            raise RuntimeError(msg) from e
        finally:
            self._mgr_ready.set()  # type: ignore[union-attr] # unblock listeners
            self._started = False

    async def start(self, *, stateless: bool = True) -> None:
        """启动 Streamable HTTP 会话管理器。"""
        async with self._start_stop_lock:
            if self._started:
                await logger.adebug("Streamable HTTP session manager already running; skipping start")
                return
            try:
                self.session_manager = StreamableHTTPSessionManager(server, stateless=stateless)
                self._mgr_ready = asyncio.Event()
                self._mgr_close = asyncio.Event()
                self._mgr_task = asyncio.create_task(self._start_session_manager())
                await self._mgr_ready.wait()
                if not self._started:  # did not start properly
                    await self._mgr_task  # await to surface the exception
            except Exception as e:
                self._cleanup()
                await logger.aexception(f"Error starting Streamable HTTP session manager: {e}")
                raise

    def get_manager(self) -> StreamableHTTPSessionManager:
        """获取可用的 Streamable HTTP 会话管理器。"""
        if not self._started or self.session_manager is None:
            raise HTTPException(status_code=503, detail="MCP Streamable HTTP transport is not initialized")
        return self.session_manager

    async def stop(self) -> None:
        """关闭 Streamable HTTP 会话管理器。"""
        async with self._start_stop_lock:
            if not self._started:
                return
            try:
                self._mgr_close.set()  # type: ignore[union-attr]
                await self._mgr_task  # type: ignore[misc]
            except Exception as e:
                await logger.aexception(f"Error stopping Streamable HTTP session manager: {e}")
                raise
            finally:
                self._cleanup()
                await logger.adebug("Streamable HTTP session manager stopped")

    def _cleanup(self) -> None:
        """Cleanup the Streamable HTTP session manager."""
        self._mgr_task = None
        self._mgr_ready = None
        self._mgr_close = None
        self.session_manager = None
        self._started = False


_streamable_http = StreamableHTTP()


async def start_streamable_http_manager(stateless: bool = True) -> None:  # noqa: FBT001, FBT002
    """启动 Streamable HTTP 会话管理器。"""
    await _streamable_http.start(stateless=stateless)


def get_streamable_http_manager() -> StreamableHTTPSessionManager:
    """获取当前 Streamable HTTP 会话管理器。"""
    return _streamable_http.get_manager()


async def stop_streamable_http_manager() -> None:
    """停止 Streamable HTTP 会话管理器。"""
    await _streamable_http.stop()


streamable_http_route_config = {  # use for all streamable http routes (except for the health check)
    "methods": ["GET", "POST", "DELETE"],
    "response_class": ResponseNoOp,
}


@router.head("/streamable", include_in_schema=False)
async def streamable_health():
    """Streamable HTTP 健康检查。"""
    return Response()


@router.api_route("/streamable", **streamable_http_route_config)
@router.api_route("/streamable/", **streamable_http_route_config)
async def handle_streamable_http(request: Request, current_user: CurrentActiveMCPUser):
    """Streamable HTTP endpoint for MCP clients that support the new transport."""
    return await _dispatch_streamable_http(request, current_user)


async def _dispatch_streamable_http(
    request: Request,
    current_user: CurrentActiveMCPUser,
) -> Response:
    """Common handler for Streamable HTTP requests with user context propagation."""
    await logger.adebug(
        "Handling %s %s via Streamable HTTP for user %s",
        request.method,
        request.url.path,
        current_user.id,
    )

    context_token = current_user_ctx.set(current_user)
    try:
        manager = get_streamable_http_manager()
        await manager.handle_request(request.scope, request.receive, request._send)  # noqa: SLF001
    except HTTPException:
        raise
    except Exception as exc:
        await logger.aexception(f"Error handling Streamable HTTP request: {exc!s}")
        raise HTTPException(status_code=500, detail="Internal server error in Streamable HTTP transport") from exc
    finally:
        current_user_ctx.reset(context_token)

    return ResponseNoOp()
