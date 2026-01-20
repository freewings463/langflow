"""
模块名称：MCP 连接与工具装配工具集

本模块提供 MCP 客户端连接、会话复用、HTTP 头校验与工具封装能力，主要用于
Langflow 与 MCP 服务器的交互。主要功能包括：
- 创建支持可选 SSL 校验的 `httpx` 客户端
- 规范化/校验请求头与 MCP 名称
- 管理 MCP 会话生命周期与清理策略
- 将 MCP 工具包装为 `StructuredTool` 以供运行

关键组件：
- `MCPSessionManager`：会话复用与清理核心
- `MCPStdioClient`/`MCPStreamableHttpClient`：两种传输模式的客户端
- `update_tools`：拉取远端工具并装配为可调用对象

设计背景：需要在长连接与多上下文之间平衡资源复用与可靠性，避免会话泄漏。
注意事项：多处逻辑依赖网络与外部进程，失败时常以 `ValueError` 反馈并记录日志。
"""

import asyncio
import contextlib
import inspect
import json
import os
import platform
import re
import shutil
import unicodedata
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import httpx
from anyio import ClosedResourceError
from httpx import codes as httpx_codes
from langchain_core.tools import StructuredTool
from mcp import ClientSession
from mcp.shared.exceptions import McpError
from pydantic import BaseModel

from lfx.log.logger import logger
from lfx.schema.json_schema import create_input_schema_from_json_schema
from lfx.services.deps import get_settings_service
from lfx.utils.async_helpers import run_until_complete

HTTP_ERROR_STATUS_CODE = httpx_codes.BAD_REQUEST

# 校验场景使用的 HTTP 状态码
HTTP_NOT_FOUND = 404
HTTP_METHOD_NOT_ALLOWED = 405
HTTP_NOT_ACCEPTABLE = 406
HTTP_BAD_REQUEST = 400
HTTP_INTERNAL_SERVER_ERROR = 500
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403

# MCP 会话管理相关配置（懒加载）
_mcp_settings_cache: dict[str, Any] = {}


def _get_mcp_setting(key: str, default: Any = None) -> Any:
    """懒加载 MCP 配置项。

    契约：输入 `key/default`，输出配置值；未命中返回 `default`。
    副作用：首次读取会访问设置服务并写入 `_mcp_settings_cache`。
    失败语义：设置服务异常将向上抛出。
    """
    if key not in _mcp_settings_cache:
        settings = get_settings_service().settings
        _mcp_settings_cache[key] = getattr(settings, key, default)
    return _mcp_settings_cache[key]


def get_max_sessions_per_server() -> int:
    """读取单服务器最大会话数。

    契约：输出整数上限，用于限制会话数量。
    副作用：读取设置服务并缓存。
    失败语义：设置服务异常将向上抛出。
    """
    return _get_mcp_setting("mcp_max_sessions_per_server")


def get_session_idle_timeout() -> int:
    """读取会话空闲超时秒数。

    契约：输出空闲超时秒数，供清理逻辑使用。
    副作用：读取设置服务并缓存。
    失败语义：设置服务异常将向上抛出。
    """
    return _get_mcp_setting("mcp_session_idle_timeout")


def get_session_cleanup_interval() -> int:
    """读取会话清理轮询间隔。

    契约：输出轮询间隔秒数。
    副作用：读取设置服务并缓存。
    失败语义：设置服务异常将向上抛出。
    """
    return _get_mcp_setting("mcp_session_cleanup_interval")


# RFC 7230 兼容的 Header 名称规则：
# 规则：`token = 1*tchar`
# 规则：`tchar = "!" / "#" / "$" / "%" / "&" / "'" / "*" / "+" / "-" / "." /
#         "^" / "_" / "`" / "|" / "~" / DIGIT / ALPHA`
HEADER_NAME_PATTERN = re.compile(r"^[!#$%&\'*+\-.0-9A-Z^_`a-z|~]+$")

# MCP 连接允许的常见 Header 白名单
ALLOWED_HEADERS = {
    "authorization",
    "accept",
    "accept-encoding",
    "accept-language",
    "cache-control",
    "content-type",
    "user-agent",
    "x-api-key",
    "x-auth-token",
    "x-custom-header",
    "x-langflow-session",
    "x-mcp-client",
    "x-requested-with",
}


def create_mcp_http_client_with_ssl_option(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
    *,
    verify_ssl: bool = True,
) -> httpx.AsyncClient:
    """创建可配置 SSL 校验的 httpx 异步客户端。

    契约：输入可选 `headers/timeout/auth/verify_ssl`，输出 `httpx.AsyncClient`。
    副作用：创建客户端对象，后续请求将携带配置并允许重定向。
    失败语义：参数类型异常由 `httpx` 构造阶段抛出。
    """
    kwargs: dict[str, Any] = {
        "follow_redirects": True,
        "verify": verify_ssl,
    }

    if timeout is None:
        kwargs["timeout"] = httpx.Timeout(30.0)
    else:
        kwargs["timeout"] = timeout

    if headers is not None:
        kwargs["headers"] = headers

    if auth is not None:
        kwargs["auth"] = auth

    return httpx.AsyncClient(**kwargs)


def validate_headers(headers: dict[str, str]) -> dict[str, str]:
    """按 RFC 7230 校验并清理 HTTP Header。

    契约：输入原始 Header 字典，输出仅包含合法且已清理的 Header。
    关键路径（三步）：
    1) 过滤非字符串键值与非法名称。
    2) 拦截注入风险并规范化名称大小写。
    3) 清理控制字符并剔除空值。

    副作用：会记录警告日志用于安全审计。
    失败语义：不抛出异常，非法项会被跳过并返回剩余结果。
    """
    if not headers:
        return {}

    sanitized_headers = {}

    for name, value in headers.items():
        if not isinstance(name, str) or not isinstance(value, str):
            logger.warning(f"Skipping non-string header: {name}={value}")
            continue

        # 安全：Header 名称需满足 RFC 7230 token 规则
        if not HEADER_NAME_PATTERN.match(name):
            logger.warning(f"Invalid header name '{name}', skipping")
            continue

        # 注意：HTTP Header 不区分大小写，统一小写便于比较
        normalized_name = name.lower()

        # 注意：仅记录非白名单 Header，保持兼容但提示风险
        if normalized_name not in ALLOWED_HEADERS:
            logger.debug(f"Using non-standard header: {normalized_name}")

        # 安全：在清理前先拦截 CR/LF 注入
        if "\r" in value or "\n" in value:
            logger.warning(f"Potential header injection detected in '{name}', skipping")
            continue

        # 安全：移除控制字符（保留制表与空格）
        sanitized_value = re.sub(r"[\x00-\x08\x0A-\x1F\x7F]", "", value)

        # 注意：统一去除首尾空白
        sanitized_value = sanitized_value.strip()

        if not sanitized_value:
            logger.warning(f"Header '{name}' has empty value after sanitization, skipping")
            continue

        sanitized_headers[normalized_name] = sanitized_value

    return sanitized_headers


def sanitize_mcp_name(name: str, max_length: int = 46) -> str:
    """清理 MCP 名称，保证可安全用作标识符。

    契约：输入原始名称与最大长度，输出仅包含字母/数字/下划线的名称。
    副作用：无。
    失败语义：清理后为空时返回默认值 `unnamed`。
    """
    if not name or not name.strip():
        return ""

    emoji_pattern = re.compile(
        "["
        "\U0001f600-\U0001f64f"  # 表情符号
        "\U0001f300-\U0001f5ff"  # 符号与象形图
        "\U0001f680-\U0001f6ff"  # 交通与地图符号
        "\U0001f1e0-\U0001f1ff"  # 国旗（iOS）
        "\U00002500-\U00002bef"  # 中文字符区间
        "\U00002702-\U000027b0"
        "\U00002702-\U000027b0"
        "\U000024c2-\U0001f251"
        "\U0001f926-\U0001f937"
        "\U00010000-\U0010ffff"
        "\u2640-\u2642"
        "\u2600-\u2b55"
        "\u200d"
        "\u23cf"
        "\u23e9"
        "\u231a"
        "\ufe0f"  # 装饰符号
        "\u3030"
        "]+",
        flags=re.UNICODE,
    )

    name = emoji_pattern.sub("", name)

    # 注意：规范化后移除变音符，避免不可见差异
    name = unicodedata.normalize("NFD", name)
    name = "".join(char for char in name if unicodedata.category(char) != "Mn")

    name = re.sub(r"[^\w\s-]", "", name)  # 仅保留字母/数字/空格/连字符
    name = re.sub(r"[-\s]+", "_", name)  # 空格与连字符统一为下划线
    name = re.sub(r"_+", "_", name)  # 合并连续下划线

    name = name.strip("_")

    if name and name[0].isdigit():
        name = f"_{name}"

    name = name.lower()

    if len(name) > max_length:
        name = name[:max_length].rstrip("_")

    if not name:
        name = "unnamed"

    return name


def _camel_to_snake(name: str) -> str:
    """将 camelCase 转为 snake_case。

    契约：输入字符串，输出 snake_case 字符串。
    副作用：无。
    失败语义：无。
    """
    import re

    # 注意：仅在小写/数字后接大写时插入下划线
    s1 = re.sub("([a-z0-9])([A-Z])", r"\1_\2", name)
    return s1.lower()


def _convert_camel_case_to_snake_case(provided_args: dict[str, Any], arg_schema: type[BaseModel]) -> dict[str, Any]:
    """按 schema 将 camelCase 字段名转换为 snake_case。

    契约：输入参数字典与 schema，输出映射后的参数字典。
    副作用：无。
    失败语义：无（未知字段原样保留，交由校验抛错）。
    """
    schema_fields = set(arg_schema.model_fields.keys())
    converted_args = {}

    for key, value in provided_args.items():
        # 注意：已存在字段名不转换
        if key in schema_fields:
            converted_args[key] = value
        else:
            snake_key = _camel_to_snake(key)
            if snake_key in schema_fields:
                converted_args[snake_key] = value
            else:
                converted_args[key] = value

    return converted_args


def _handle_tool_validation_error(
    e: Exception, tool_name: str, provided_args: dict[str, Any], arg_schema: type[BaseModel]
) -> None:
    """统一处理工具参数校验错误。

    契约：输入异常与上下文信息，输出为 `None`，但会抛 `ValueError`。
    副作用：无。
    失败语义：始终抛 `ValueError`，附带更易理解的错误信息。
    """
    # 注意：当调用方未传参但 schema 有必填字段时，返回更明确的提示
    if not provided_args and hasattr(arg_schema, "model_fields"):
        required_fields = [name for name, field in arg_schema.model_fields.items() if field.is_required()]
        if required_fields:
            msg = (
                f"Tool '{tool_name}' requires arguments but none were provided. "
                f"Required fields: {', '.join(required_fields)}. "
                f"Please check that the LLM is properly calling the tool with arguments."
            )
            raise ValueError(msg) from e
    msg = f"Invalid input: {e}"
    raise ValueError(msg) from e


def create_tool_coroutine(tool_name: str, arg_schema: type[BaseModel], client) -> Callable[..., Awaitable]:
    """构造异步工具调用协程。

    契约：输入工具名、参数 schema 与 MCP 客户端，输出可 await 的调用函数。
    副作用：调用时会触发 MCP 远程执行。
    失败语义：参数校验失败或执行失败时抛 `ValueError`。
    """
    async def tool_coroutine(*args, **kwargs):
        field_names = list(arg_schema.model_fields.keys())
        provided_args = {}
        for i, arg in enumerate(args):
            if i >= len(field_names):
                msg = "Too many positional arguments provided"
                raise ValueError(msg)
            provided_args[field_names[i]] = arg
        provided_args.update(kwargs)
        provided_args = _convert_camel_case_to_snake_case(provided_args, arg_schema)
        try:
            validated = arg_schema.model_validate(provided_args)
        except Exception as e:  # noqa: BLE001
            _handle_tool_validation_error(e, tool_name, provided_args, arg_schema)

        try:
            return await client.run_tool(tool_name, arguments=validated.model_dump())
        except Exception as e:
            await logger.aerror(f"Tool '{tool_name}' execution failed: {e}")
            msg = f"Tool '{tool_name}' execution failed: {e}"
            raise ValueError(msg) from e

    return tool_coroutine


def create_tool_func(tool_name: str, arg_schema: type[BaseModel], client) -> Callable[..., str]:
    """构造同步工具调用函数。

    契约：输入工具名、参数 schema 与 MCP 客户端，输出同步调用函数。
    副作用：调用时会触发 MCP 远程执行（内部使用 `run_until_complete`）。
    失败语义：参数校验失败或执行失败时抛 `ValueError`。
    """
    def tool_func(*args, **kwargs):
        field_names = list(arg_schema.model_fields.keys())
        provided_args = {}
        for i, arg in enumerate(args):
            if i >= len(field_names):
                msg = "Too many positional arguments provided"
                raise ValueError(msg)
            provided_args[field_names[i]] = arg
        provided_args.update(kwargs)
        provided_args = _convert_camel_case_to_snake_case(provided_args, arg_schema)
        try:
            validated = arg_schema.model_validate(provided_args)
        except Exception as e:  # noqa: BLE001
            _handle_tool_validation_error(e, tool_name, provided_args, arg_schema)

        try:
            return run_until_complete(client.run_tool(tool_name, arguments=validated.model_dump()))
        except Exception as e:
            logger.error(f"Tool '{tool_name}' execution failed: {e}")
            msg = f"Tool '{tool_name}' execution failed: {e}"
            raise ValueError(msg) from e

    return tool_func


def get_unique_name(base_name, max_length, existing_names):
    """生成不冲突的名称。

    契约：输入基础名称、最大长度与已存在集合，输出唯一名称。
    副作用：无。
    失败语义：无（保证返回可用名称）。
    """
    name = base_name[:max_length]
    if name not in existing_names:
        return name
    i = 1
    while True:
        suffix = f"_{i}"
        truncated_base = base_name[: max_length - len(suffix)]
        candidate = f"{truncated_base}{suffix}"
        if candidate not in existing_names:
            return candidate
        i += 1


async def get_flow_snake_case(flow_name: str, user_id: str, session, *, is_action: bool | None = None):
    """按 snake_case 名称查找用户 Flow。

    契约：输入 `flow_name/user_id/session`，输出匹配的 Flow 或 `None`。
    副作用：访问数据库会话执行查询。
    失败语义：缺少 Flow 模型时抛 `ImportError`。
    """
    try:
        from langflow.services.database.models.flow.model import Flow
        from sqlmodel import select
    except ImportError as e:
        msg = "Langflow Flow model is not available. This feature requires the full Langflow installation."
        raise ImportError(msg) from e

    uuid_user_id = UUID(user_id) if isinstance(user_id, str) else user_id

    stmt = select(Flow).where(Flow.user_id == uuid_user_id).where(Flow.is_component == False)  # noqa: E712
    flows = (await session.exec(stmt)).all()

    for flow in flows:
        if is_action and flow.action_name:
            this_flow_name = sanitize_mcp_name(flow.action_name)
        else:
            this_flow_name = sanitize_mcp_name(flow.name)

        if this_flow_name == flow_name:
            return flow
    return None


def _is_valid_key_value_item(item: Any) -> bool:
    """判断是否为 `{"key": ..., "value": ...}` 结构。"""
    return isinstance(item, dict) and "key" in item and "value" in item


def _process_headers(headers: Any) -> dict:
    """将多种 Header 输入统一为合法字典。

    契约：输入可能为 dict/list/None，输出合法 Header 字典。
    副作用：调用 `validate_headers` 进行清理与日志记录。
    失败语义：结构异常时返回 `{}`。
    """
    if headers is None:
        return {}
    if isinstance(headers, dict):
        return validate_headers(headers)
    if isinstance(headers, list):
        processed_headers = {}
        try:
            for item in headers:
                if not _is_valid_key_value_item(item):
                    continue
                key = item["key"]
                value = item["value"]
                processed_headers[key] = value
        except (KeyError, TypeError, ValueError):
            return {}  # 注意：异常时返回空字典而非 None
        return validate_headers(processed_headers)
    return {}


def _validate_node_installation(command: str) -> str:
    """校验 `npx` 命令依赖是否可用。

    契约：输入命令字符串，输出原命令字符串。
    副作用：无。
    失败语义：未安装 Node.js 时抛 `ValueError`。
    """
    if "npx" in command and not shutil.which("node"):
        msg = "Node.js is not installed. Please install Node.js to use npx commands."
        raise ValueError(msg)
    return command


async def _validate_connection_params(mode: str, command: str | None = None, url: str | None = None) -> None:
    """按连接模式校验参数完整性。

    契约：输入 `mode` 与相关参数，输出 `None` 表示校验通过。
    副作用：无。
    失败语义：参数缺失或模式非法时抛 `ValueError`。
    """
    if mode not in ["Stdio", "Streamable_HTTP", "SSE"]:
        msg = f"Invalid mode: {mode}. Must be either 'Stdio', 'Streamable_HTTP', or 'SSE'"
        raise ValueError(msg)

    if mode == "Stdio" and not command:
        msg = "Command is required for Stdio mode"
        raise ValueError(msg)
    if mode == "Stdio" and command:
        _validate_node_installation(command)
    if mode in ["Streamable_HTTP", "SSE"] and not url:
        msg = f"URL is required for {mode} mode"
        raise ValueError(msg)


class MCPSessionManager:
    """管理 MCP 持久会话与清理策略。

    契约：以服务器维度复用会话，并限制单服务器会话数量。
    副作用：创建后台清理任务，可能启动/关闭子进程与网络连接。
    失败语义：连接失败会在调用链上抛 `ValueError`，清理失败仅记录日志。
    注意：缓存传输方式偏好以减少重复失败重试。
    """

    def __init__(self):
        # 注意：`sessions_by_server` 结构为 `server_key -> {"sessions": {session_id: info}, "last_cleanup": ts}`
        self.sessions_by_server = {}
        self._background_tasks = set()  # 注意：保留任务引用，避免被 GC 取消
        # 注意：兼容旧逻辑：context_id -> (server_key, session_id)
        self._context_to_session: dict[str, tuple[str, str]] = {}
        # 注意：记录每个会话被多少上下文引用
        self._session_refcount: dict[tuple[str, str], int] = {}
        # 注意：缓存可用传输方式，避免重复失败重试
        # `server_key -> "streamable_http" | "sse"`
        self._transport_preference: dict[str, str] = {}
        self._cleanup_task = None
        self._start_cleanup_task()

    def _start_cleanup_task(self):
        """启动周期性清理任务。

        契约：仅在未运行时创建后台任务。
        副作用：创建 asyncio 任务并加入 `_background_tasks`。
        失败语义：异常由事件循环抛出。
        """
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._periodic_cleanup())
            self._background_tasks.add(self._cleanup_task)
            self._cleanup_task.add_done_callback(self._background_tasks.discard)

    async def _periodic_cleanup(self):
        """周期性清理空闲会话。

        契约：循环执行清理，直到任务被取消。
        副作用：可能关闭会话并记录日志。
        失败语义：可恢复异常被吞并并记录，任务继续运行。
        """
        while True:
            try:
                await asyncio.sleep(get_session_cleanup_interval())
                await self._cleanup_idle_sessions()
            except asyncio.CancelledError:
                break
            except (RuntimeError, KeyError, ClosedResourceError, ValueError, asyncio.TimeoutError) as e:
                # 注意：可恢复异常仅记录，不中断清理循环
                await logger.awarning(f"Error in periodic cleanup: {e}")

    async def _cleanup_idle_sessions(self):
        """清理超时空闲的会话。

        契约：遍历各服务器会话并移除超时项。
        副作用：可能关闭会话并删除缓存条目。
        失败语义：异常由调用方处理（通常在周期任务中记录后继续）。
        """
        current_time = asyncio.get_event_loop().time()
        servers_to_remove = []

        for server_key, server_data in self.sessions_by_server.items():
            sessions = server_data.get("sessions", {})
            sessions_to_remove = []

            for session_id, session_info in list(sessions.items()):
                if current_time - session_info["last_used"] > get_session_idle_timeout():
                    sessions_to_remove.append(session_id)

            for session_id in sessions_to_remove:
                await logger.ainfo(f"Cleaning up idle session {session_id} for server {server_key}")
                await self._cleanup_session_by_id(server_key, session_id)

            if not sessions:
                servers_to_remove.append(server_key)

        for server_key in servers_to_remove:
            del self.sessions_by_server[server_key]

    def _get_server_key(self, connection_params, transport_type: str) -> str:
        """根据连接参数生成可复用的 server_key。

        契约：输入连接参数与传输类型，输出稳定的 server_key 字符串。
        副作用：无。
        失败语义：无（使用兜底 hash）。
        """
        if transport_type == "stdio":
            if hasattr(connection_params, "command"):
                # 注意：stdio 模式需包含命令、参数与环境以区分实例
                command_str = f"{connection_params.command} {' '.join(connection_params.args or [])}"
                env_str = str(sorted((connection_params.env or {}).items()))
                key_input = f"{command_str}|{env_str}"
                return f"stdio_{hash(key_input)}"
        elif transport_type == "streamable_http" and (
            isinstance(connection_params, dict) and "url" in connection_params
        ):
            # 注意：HTTP 模式需包含 URL 与 Header 以区分实例
            url = connection_params["url"]
            headers = str(sorted((connection_params.get("headers", {})).items()))
            key_input = f"{url}|{headers}"
            return f"streamable_http_{hash(key_input)}"

        # 注意：兜底使用完整参数 hash
        return f"{transport_type}_{hash(str(connection_params))}"

    async def _validate_session_connectivity(self, session) -> bool:
        """通过轻量操作验证会话可用性。

        契约：输入会话对象，输出布尔值表示可用性。
        副作用：调用 `list_tools` 触发一次远程请求。
        失败语义：连接异常返回 `False`，未知异常向上抛出。
        """
        try:
            # 注意：使用 `list_tools` 作为轻量连通性探测，并缩短超时
            response = await asyncio.wait_for(session.list_tools(), timeout=3.0)
        except (asyncio.TimeoutError, ConnectionError, OSError, ValueError) as e:
            await logger.adebug(f"Session connectivity test failed (standard error): {e}")
            return False
        except Exception as e:
            # 注意：补充 MCP 特有错误判断
            error_str = str(e)
            if (
                "ClosedResourceError" in str(type(e))
                or "Connection closed" in error_str
                or "Connection lost" in error_str
                or "Connection failed" in error_str
                or "Transport closed" in error_str
                or "Stream closed" in error_str
            ):
                await logger.adebug(f"Session connectivity test failed (MCP connection error): {e}")
                return False
            # 注意：未知异常向上抛出，避免误判健康
            await logger.awarning(f"Unexpected error in connectivity test: {e}")
            raise
        else:
            # 注意：响应对象需包含 tools 字段
            if response is None:
                await logger.adebug("Session connectivity test failed: received None response")
                return False
            try:
                tools = getattr(response, "tools", None)
                if tools is None:
                    await logger.adebug("Session connectivity test failed: no tools attribute in response")
                    return False
            except (AttributeError, TypeError) as e:
                await logger.adebug(f"Session connectivity test failed while validating response: {e}")
                return False
            else:
                await logger.adebug(f"Session connectivity test passed: found {len(tools)} tools")
                return True

    async def get_session(self, context_id: str, connection_params, transport_type: str):
        """获取或创建可复用的会话。

        契约：输入 `context_id/connection_params/transport_type`，输出可用会话对象。
        关键路径（三步）：
        1) 基于连接参数生成 `server_key` 并复用健康会话。
        2) 超过上限时移除最久未用会话。
        3) 创建新会话并记录映射与引用计数。

        异常流：创建会话或健康检查失败将抛 `ValueError`。
        性能瓶颈：`list_tools` 健康检查与会话创建的网络/进程开销。
        排障入口：日志关键字 `Reusing existing session` / `Creating new session`。
        """
        server_key = self._get_server_key(connection_params, transport_type)

        if server_key not in self.sessions_by_server:
            self.sessions_by_server[server_key] = {"sessions": {}, "last_cleanup": asyncio.get_event_loop().time()}

        server_data = self.sessions_by_server[server_key]
        sessions = server_data["sessions"]

        # 注意：优先复用健康会话
        for session_id, session_info in list(sessions.items()):
            session = session_info["session"]
            task = session_info["task"]

            if not task.done():
                session_info["last_used"] = asyncio.get_event_loop().time()

                if await self._validate_session_connectivity(session):
                    await logger.adebug(f"Reusing existing session {session_id} for server {server_key}")
                    # 注意：记录映射并增加引用计数，兼容旧清理逻辑
                    self._context_to_session[context_id] = (server_key, session_id)
                    self._session_refcount[(server_key, session_id)] = (
                        self._session_refcount.get((server_key, session_id), 0) + 1
                    )
                    return session
                await logger.ainfo(f"Session {session_id} for server {server_key} failed health check, cleaning up")
                await self._cleanup_session_by_id(server_key, session_id)
            else:
                await logger.ainfo(f"Session {session_id} for server {server_key} task is done, cleaning up")
                await self._cleanup_session_by_id(server_key, session_id)

        # 注意：超过上限时移除最久未用会话
        if len(sessions) >= get_max_sessions_per_server():
            oldest_session_id = min(sessions.keys(), key=lambda x: sessions[x]["last_used"])
            await logger.ainfo(
                f"Maximum sessions reached for server {server_key}, removing oldest session {oldest_session_id}"
            )
            await self._cleanup_session_by_id(server_key, oldest_session_id)

        session_id = f"{server_key}_{len(sessions)}"
        await logger.ainfo(f"Creating new session {session_id} for server {server_key}")

        if transport_type == "stdio":
            session, task = await self._create_stdio_session(session_id, connection_params)
            actual_transport = "stdio"
        elif transport_type == "streamable_http":
            # 注意：优先复用已验证的传输方式
            preferred_transport = self._transport_preference.get(server_key)
            session, task, actual_transport = await self._create_streamable_http_session(
                session_id, connection_params, preferred_transport
            )
            # 注意：缓存成功的传输方式，减少重试
            self._transport_preference[server_key] = actual_transport
        else:
            msg = f"Unknown transport type: {transport_type}"
            raise ValueError(msg)

        sessions[session_id] = {
            "session": session,
            "task": task,
            "type": actual_transport,
            "last_used": asyncio.get_event_loop().time(),
        }

        self._context_to_session[context_id] = (server_key, session_id)
        self._session_refcount[(server_key, session_id)] = 1

        return session

    async def _create_stdio_session(self, session_id: str, connection_params):
        """创建 stdio 会话并以后台任务维持。

        契约：输入 `session_id/connection_params`，输出 `(session, task)`。
        关键路径（三步）：
        1) 创建后台任务并初始化 MCP 会话。
        2) 等待会话就绪信号（带超时）。
        3) 返回会话与后台任务引用。

        异常流：初始化超时抛 `ValueError` 并清理任务。
        性能瓶颈：子进程启动与会话初始化。
        排障入口：日志关键字 `Timeout waiting for STDIO session`。
        """
        import asyncio

        from mcp.client.stdio import stdio_client

        session_future: asyncio.Future[ClientSession] = asyncio.Future()

        async def session_task():
            """后台任务：初始化并维持会话存活。"""
            try:
                async with stdio_client(connection_params) as (read, write):
                    session = ClientSession(read, write)
                    async with session:
                        await session.initialize()
                        session_future.set_result(session)

                        import anyio

                        event = anyio.Event()
                        try:
                            await event.wait()
                        except asyncio.CancelledError:
                            await logger.ainfo(f"Session {session_id} is shutting down")
            except Exception as e:  # noqa: BLE001
                if not session_future.done():
                    session_future.set_exception(e)

        task = asyncio.create_task(session_task())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

        try:
            session = await asyncio.wait_for(session_future, timeout=30.0)
        except asyncio.TimeoutError as timeout_err:
            if not task.done():
                task.cancel()
                import contextlib

                with contextlib.suppress(asyncio.CancelledError):
                    await task
            self._background_tasks.discard(task)
            msg = f"Timeout waiting for STDIO session {session_id} to initialize"
            await logger.aerror(msg)
            raise ValueError(msg) from timeout_err

        return session, task

    async def _create_streamable_http_session(
        self, session_id: str, connection_params, preferred_transport: str | None = None
    ):
        """创建 Streamable HTTP 会话，失败时回退 SSE。

        契约：输入 `session_id/connection_params/preferred_transport`，输出 `(session, task, transport_used)`。
        关键路径（三步）：
        1) 优先尝试 Streamable HTTP（可选快速超时）。
        2) 失败则回退 SSE 并记录成功传输方式。
        3) 返回会话与后台任务引用。

        异常流：两种传输均失败时抛 `ValueError`。
        性能瓶颈：网络握手与会话初始化。
        排障入口：日志关键字 `Streamable HTTP` / `SSE connection failed`。
        """
        import asyncio

        from mcp.client.sse import sse_client
        from mcp.client.streamable_http import streamablehttp_client

        session_future: asyncio.Future[ClientSession] = asyncio.Future()
        used_transport: list[str] = []

        verify_ssl = connection_params.get("verify_ssl", True)

        def custom_httpx_factory(
            headers: dict[str, str] | None = None,
            timeout: httpx.Timeout | None = None,
            auth: httpx.Auth | None = None,
        ) -> httpx.AsyncClient:
            return create_mcp_http_client_with_ssl_option(
                headers=headers, timeout=timeout, auth=auth, verify_ssl=verify_ssl
            )

        async def session_task():
            """后台任务：初始化并维持会话存活。"""
            streamable_error = None

            # 注意：若已缓存 SSE 成功，直接跳过 Streamable HTTP
            if preferred_transport != "sse":
                try:
                    await logger.adebug(f"Attempting Streamable HTTP connection for session {session_id}")
                    async with streamablehttp_client(
                        url=connection_params["url"],
                        headers=connection_params["headers"],
                        timeout=connection_params["timeout_seconds"],
                        httpx_client_factory=custom_httpx_factory,
                    ) as (read, write, _):
                        session = ClientSession(read, write)
                        async with session:
                            await asyncio.wait_for(session.initialize(), timeout=2.0)
                            used_transport.append("streamable_http")
                            await logger.ainfo(f"Session {session_id} connected via Streamable HTTP")
                            session_future.set_result(session)

                            import anyio

                            event = anyio.Event()
                            try:
                                await event.wait()
                            except asyncio.CancelledError:
                                await logger.ainfo(f"Session {session_id} (Streamable HTTP) is shutting down")
                except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001
                    # 注意：Streamable HTTP 失败或超时立即回退 SSE
                    streamable_error = e
                    error_type = "timed out" if isinstance(e, asyncio.TimeoutError) else "failed"
                    await logger.awarning(
                        f"Streamable HTTP {error_type} for session {session_id}: {e}. Falling back to SSE..."
                    )
            else:
                await logger.adebug(f"Skipping Streamable HTTP for session {session_id}, using cached SSE preference")

            # 注意：Streamable HTTP 失败或偏好 SSE 时执行回退
            if streamable_error is not None or preferred_transport == "sse":
                try:
                    await logger.adebug(f"Attempting SSE connection for session {session_id}")
                    sse_read_timeout = connection_params.get("sse_read_timeout_seconds", 30)

                    async with sse_client(
                        connection_params["url"],
                        connection_params["headers"],
                        connection_params["timeout_seconds"],
                        sse_read_timeout,
                        httpx_client_factory=custom_httpx_factory,
                    ) as (read, write):
                        session = ClientSession(read, write)
                        async with session:
                            await session.initialize()
                            used_transport.append("sse")
                            fallback_msg = " (fallback)" if streamable_error else " (preferred)"
                            await logger.ainfo(f"Session {session_id} connected via SSE{fallback_msg}")
                            if not session_future.done():
                                session_future.set_result(session)

                            import anyio

                            event = anyio.Event()
                            try:
                                await event.wait()
                            except asyncio.CancelledError:
                                await logger.ainfo(f"Session {session_id} (SSE) is shutting down")
                except Exception as sse_error:  # noqa: BLE001
                    # 注意：两种传输均失败时抛出错误
                    if streamable_error:
                        await logger.aerror(
                            f"Both Streamable HTTP and SSE failed for session {session_id}. "
                            f"Streamable HTTP error: {streamable_error}. SSE error: {sse_error}"
                        )
                        if not session_future.done():
                            session_future.set_exception(
                                ValueError(
                                    f"Failed to connect via Streamable HTTP ({streamable_error}) or SSE ({sse_error})"
                                )
                            )
                    else:
                        await logger.aerror(f"SSE connection failed for session {session_id}: {sse_error}")
                        if not session_future.done():
                            session_future.set_exception(ValueError(f"Failed to connect via SSE: {sse_error}"))

        task = asyncio.create_task(session_task())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

        try:
            session = await asyncio.wait_for(session_future, timeout=30.0)
            if used_transport:
                transport_used = used_transport[0]
                await logger.ainfo(f"Session {session_id} successfully established using {transport_used}")
                return session, task, transport_used
            msg = f"Session {session_id} established but transport not recorded"
            raise ValueError(msg)
        except asyncio.TimeoutError as timeout_err:
            if not task.done():
                task.cancel()
                import contextlib

                with contextlib.suppress(asyncio.CancelledError):
                    await task
            self._background_tasks.discard(task)
            msg = f"Timeout waiting for Streamable HTTP/SSE session {session_id} to initialize"
            await logger.aerror(msg)
            raise ValueError(msg) from timeout_err

    async def _cleanup_session_by_id(self, server_key: str, session_id: str):
        """按 server_key 与 session_id 清理会话。

        契约：输入标识符，输出 `None`，并确保会话资源被释放。
        关键路径（三步）：
        1) 定位会话与兼容旧结构。
        2) 尝试关闭会话并取消后台任务。
        3) 移除缓存条目并记录异常。

        副作用：关闭会话、取消任务并修改缓存结构。
        失败语义：清理失败仅记录日志。
        """
        if server_key not in self.sessions_by_server:
            return

        server_data = self.sessions_by_server[server_key]
        # 注意：兼容旧结构（sessions 可能直接挂在 server_data 上）
        if isinstance(server_data, dict) and "sessions" in server_data:
            sessions = server_data["sessions"]
        else:
            sessions = server_data

        if session_id not in sessions:
            return

        session_info = sessions[session_id]
        try:
            # 注意：优先尝试优雅关闭会话
            if "session" in session_info:
                session = session_info["session"]

                # 注意：优先使用 `aclose`
                if hasattr(session, "aclose"):
                    try:
                        await session.aclose()
                        await logger.adebug("Successfully closed session %s using aclose()", session_id)
                    except Exception as e:  # noqa: BLE001
                        await logger.adebug("Error closing session %s with aclose(): %s", session_id, e)

                # 注意：否则回退 `close`
                elif hasattr(session, "close"):
                    try:
                        # 注意：根据是否可 await 选择调用方式
                        if inspect.iscoroutinefunction(session.close):
                            await session.close()
                            await logger.adebug("Successfully closed session %s using async close()", session_id)
                        else:
                            close_result = session.close()
                            if inspect.isawaitable(close_result):
                                await close_result
                                await logger.adebug(
                                    "Successfully closed session %s using awaitable close()", session_id
                                )
                            else:
                                await logger.adebug("Successfully closed session %s using sync close()", session_id)
                    except Exception as e:  # noqa: BLE001
                        await logger.adebug("Error closing session %s with close(): %s", session_id, e)

            # 注意：取消后台任务触发会话关闭
            if "task" in session_info:
                task = session_info["task"]
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        await logger.ainfo(f"Cancelled task for session {session_id}")
        except Exception as e:  # noqa: BLE001
            await logger.awarning(f"Error cleaning up session {session_id}: {e}")
        finally:
            del sessions[session_id]

    async def cleanup_all(self):
        """清理所有会话并关闭后台任务。

        契约：清空所有会话与缓存映射。
        关键路径（三步）：
        1) 停止周期清理任务。
        2) 逐服务器清理会话与后台任务。
        3) 清空缓存与兼容映射。

        副作用：取消后台任务并关闭所有连接。
        失败语义：清理失败仅记录日志。
        """
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task

        for server_key in list(self.sessions_by_server.keys()):
            server_data = self.sessions_by_server[server_key]
            if isinstance(server_data, dict) and "sessions" in server_data:
                sessions = server_data["sessions"]
            else:
                sessions = server_data

            for session_id in list(sessions.keys()):
                await self._cleanup_session_by_id(server_key, session_id)

        self.sessions_by_server.clear()

        self._context_to_session.clear()
        self._session_refcount.clear()

        for task in list(self._background_tasks):
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        # 注意：留出短暂延迟，减少子进程清理警告
        await asyncio.sleep(0.5)

    async def _cleanup_session(self, context_id: str):
        """按 context_id 进行兼容清理。

        契约：基于 `context_id` 递减引用计数，最后一个引用释放会话。
        副作用：可能关闭会话并更新缓存映射。
        失败语义：找不到映射则记录调试日志并返回。
        """
        mapping = self._context_to_session.get(context_id)
        if not mapping:
            await logger.adebug(f"No session mapping found for context_id {context_id}")
            return

        server_key, session_id = mapping
        ref_key = (server_key, session_id)
        remaining = self._session_refcount.get(ref_key, 1) - 1

        if remaining <= 0:
            await self._cleanup_session_by_id(server_key, session_id)
            self._session_refcount.pop(ref_key, None)
        else:
            self._session_refcount[ref_key] = remaining

        # 注意：移除该 context 的映射
        self._context_to_session.pop(context_id, None)


class MCPStdioClient:
    """MCP stdio 传输客户端封装。

    契约：提供连接、工具调用与会话复用能力。
    副作用：可能启动子进程并建立 MCP 会话。
    失败语义：连接或调用失败时抛 `ValueError`。
    """
    def __init__(self, component_cache=None):
        self.session: ClientSession | None = None
        self._connection_params = None
        self._connected = False
        self._session_context: str | None = None
        self._component_cache = component_cache

    async def _connect_to_server(self, command_str: str, env: dict[str, str] | None = None) -> list[StructuredTool]:
        """使用 stdio 方式连接 MCP 服务器（内部实现）。

        契约：输入命令与环境变量，输出工具列表。
        关键路径（三步）：
        1) 生成平台适配的启动参数与环境变量。
        2) 获取/创建持久会话并列出工具。
        3) 标记连接状态并返回工具列表。

        副作用：启动子进程并建立 MCP 会话。
        失败语义：连接失败抛 `ValueError` 或底层异常。
        """
        from mcp import StdioServerParameters

        command = command_str.split(" ")
        env_data: dict[str, str] = {"DEBUG": "true", "PATH": os.environ["PATH"], **(env or {})}

        if platform.system() == "Windows":
            server_params = StdioServerParameters(
                command="cmd",
                args=[
                    "/c",
                    f"{command[0]} {' '.join(command[1:])} || echo Command failed with exit code %errorlevel% 1>&2",
                ],
                env=env_data,
            )
        else:
            server_params = StdioServerParameters(
                command="bash",
                args=["-c", f"exec {command_str} || echo 'Command failed with exit code $?' >&2"],
                env=env_data,
            )

        self._connection_params = server_params

        if not self._session_context:
            import uuid

            param_hash = uuid.uuid4().hex[:8]
            self._session_context = f"default_{param_hash}"

        session = await self._get_or_create_session()
        response = await session.list_tools()
        self._connected = True
        return response.tools

    async def connect_to_server(self, command_str: str, env: dict[str, str] | None = None) -> list[StructuredTool]:
        """使用 stdio 方式连接 MCP 服务器（对外接口）。

        契约：输入命令与环境变量，输出工具列表。
        副作用：启动子进程并建立 MCP 会话。
        失败语义：超时或连接失败抛 `ValueError`。
        """
        return await asyncio.wait_for(
            self._connect_to_server(command_str, env), timeout=get_settings_service().settings.mcp_server_timeout
        )

    def set_session_context(self, context_id: str):
        """设置会话上下文标识。

        契约：输入 `context_id`，用于会话复用与清理。
        副作用：覆盖当前上下文。
        失败语义：无。
        """
        self._session_context = context_id

    def _get_session_manager(self) -> MCPSessionManager:
        """获取或创建会话管理器。

        契约：优先从组件缓存读取，否则创建新实例。
        副作用：可能写入组件缓存。
        失败语义：缓存访问异常将向上抛出。
        """
        if not self._component_cache:
            # 注意：无缓存时使用实例级管理器
            if not hasattr(self, "_session_manager"):
                self._session_manager = MCPSessionManager()
            return self._session_manager

        from lfx.services.cache.utils import CacheMiss

        session_manager = self._component_cache.get("mcp_session_manager")
        if isinstance(session_manager, CacheMiss):
            session_manager = MCPSessionManager()
            self._component_cache.set("mcp_session_manager", session_manager)
        return session_manager

    async def _get_or_create_session(self) -> ClientSession:
        """为当前上下文获取或创建会话。

        契约：输出可用 `ClientSession`。
        副作用：可能创建新会话并缓存。
        失败语义：缺少上下文或连接参数时抛 `ValueError`。
        """
        if not self._session_context or not self._connection_params:
            msg = "Session context and connection params must be set"
            raise ValueError(msg)

        session_manager = self._get_session_manager()
        return await session_manager.get_session(self._session_context, self._connection_params, "stdio")

    async def run_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """在当前会话上下文中执行工具。

        契约：输入 `tool_name/arguments`，输出工具执行结果。
        关键路径（三步）：
        1) 确保已连接并补齐默认 context。
        2) 获取/创建会话并调用工具。
        3) 失败时按错误类型重试或抛错。

        异常流：连接不可用或执行失败抛 `ValueError`。
        性能瓶颈：远程调用超时与会话重建。
        排障入口：日志关键字 `Tool '{tool_name}' failed`。
        """
        if not self._connected or not self._connection_params:
            msg = "Session not initialized or disconnected. Call connect_to_server first."
            raise ValueError(msg)

        if not self._session_context:
            import uuid

            param_hash = uuid.uuid4().hex[:8]
            self._session_context = f"default_{param_hash}"

        max_retries = 2
        last_error_type = None

        for attempt in range(max_retries):
            try:
                await logger.adebug(f"Attempting to run tool '{tool_name}' (attempt {attempt + 1}/{max_retries})")
                session = await self._get_or_create_session()

                result = await asyncio.wait_for(
                    session.call_tool(tool_name, arguments=arguments),
                    timeout=30.0,
                )
            except Exception as e:
                current_error_type = type(e).__name__
                await logger.awarning(f"Tool '{tool_name}' failed on attempt {attempt + 1}: {current_error_type} - {e}")

                # 注意：识别常见 MCP 连接错误类型
                try:
                    is_closed_resource_error = isinstance(e, ClosedResourceError)
                    is_mcp_connection_error = isinstance(e, McpError) and "Connection closed" in str(e)
                except ImportError:
                    is_closed_resource_error = "ClosedResourceError" in str(type(e))
                    is_mcp_connection_error = "Connection closed" in str(e)

                is_timeout_error = isinstance(e, asyncio.TimeoutError | TimeoutError)

                if last_error_type == current_error_type and attempt > 0:
                    await logger.aerror(f"Repeated {current_error_type} error for tool '{tool_name}', not retrying")
                    break

                last_error_type = current_error_type

                if (is_closed_resource_error or is_mcp_connection_error) and attempt < max_retries - 1:
                    await logger.awarning(
                        f"MCP session connection issue for tool '{tool_name}', retrying with fresh session..."
                    )
                    if self._session_context:
                        session_manager = self._get_session_manager()
                        await session_manager._cleanup_session(self._session_context)
                    await asyncio.sleep(0.5)
                    continue

                if is_timeout_error and attempt < max_retries - 1:
                    await logger.awarning(f"Tool '{tool_name}' timed out, retrying...")
                    await asyncio.sleep(1.0)
                    continue

                if (
                    isinstance(e, ConnectionError | TimeoutError | OSError | ValueError)
                    or is_closed_resource_error
                    or is_mcp_connection_error
                    or is_timeout_error
                ):
                    msg = f"Failed to run tool '{tool_name}' after {attempt + 1} attempts: {e}"
                    await logger.aerror(msg)
                    if self._session_context and self._component_cache:
                        cache_key = f"mcp_session_stdio_{self._session_context}"
                        self._component_cache.delete(cache_key)
                    self._connected = False
                    raise ValueError(msg) from e
                raise
            else:
                await logger.adebug(f"Tool '{tool_name}' completed successfully")
                return result

        msg = f"Failed to run tool '{tool_name}': Maximum retries exceeded with repeated {last_error_type} errors"
        await logger.aerror(msg)
        raise ValueError(msg)

    async def disconnect(self):
        """断开连接并清理本地会话。

        契约：释放当前会话与连接参数。
        副作用：取消后台任务并清理缓存映射。
        失败语义：清理失败仅记录日志。
        """
        if self._session_context:
            session_manager = self._get_session_manager()
            await session_manager._cleanup_session(self._session_context)

        self.session = None
        self._connection_params = None
        self._connected = False
        self._session_context = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()


class MCPStreamableHttpClient:
    """MCP Streamable HTTP/SSE 客户端封装。

    契约：提供基于 HTTP 的连接、工具调用与会话复用能力。
    副作用：建立网络连接并可能发送会话终止请求。
    失败语义：连接或调用失败时抛 `ValueError`。
    """
    def __init__(self, component_cache=None):
        self.session: ClientSession | None = None
        self._connection_params = None
        self._connected = False
        self._session_context: str | None = None
        self._component_cache = component_cache

    def _get_session_manager(self) -> MCPSessionManager:
        """获取或创建会话管理器。

        契约：优先从组件缓存读取，否则创建新实例。
        副作用：可能写入组件缓存。
        失败语义：缓存访问异常将向上抛出。
        """
        if not self._component_cache:
            # 注意：无缓存时使用实例级管理器
            if not hasattr(self, "_session_manager"):
                self._session_manager = MCPSessionManager()
            return self._session_manager

        from lfx.services.cache.utils import CacheMiss

        session_manager = self._component_cache.get("mcp_session_manager")
        if isinstance(session_manager, CacheMiss):
            session_manager = MCPSessionManager()
            self._component_cache.set("mcp_session_manager", session_manager)
        return session_manager

    async def validate_url(self, url: str | None) -> tuple[bool, str]:
        """校验 Streamable HTTP/SSE URL 合法性。

        契约：输入 `url`，输出 `(is_valid, error_msg)`。
        副作用：无。
        失败语义：解析异常返回 `False` 与错误信息。
        """
        try:
            parsed = urlparse(url)
            if not parsed.scheme or not parsed.netloc:
                return False, "Invalid URL format. Must include scheme (http/https) and host."
        except (ValueError, OSError) as e:
            return False, f"URL validation error: {e!s}"
        return True, ""

    async def _connect_to_server(
        self,
        url: str | None,
        headers: dict[str, str] | None = None,
        timeout_seconds: int = 30,
        sse_read_timeout_seconds: int = 30,
        *,
        verify_ssl: bool = True,
    ) -> list[StructuredTool]:
        """使用 Streamable HTTP 连接 MCP 服务器（内部实现，支持 SSE 回退）。

        契约：输入 URL 与可选 Header/超时参数，输出工具列表。
        关键路径（三步）：
        1) 清理 Header 并校验 URL（首次连接）。
        2) 建立持久会话（HTTP 失败时回退 SSE）。
        3) 拉取工具列表并标记连接状态。

        副作用：建立网络连接并创建会话。
        失败语义：URL 非法或连接失败抛 `ValueError`。
        """
        validated_headers = _process_headers(headers)

        if url is None:
            msg = "URL is required for StreamableHTTP or SSE mode"
            raise ValueError(msg)

        if not self._connected or not self._connection_params:
            is_valid, error_msg = await self.validate_url(url)
            if not is_valid:
                msg = f"Invalid Streamable HTTP or SSE URL ({url}): {error_msg}"
                raise ValueError(msg)
            self._connection_params = {
                "url": url,
                "headers": validated_headers,
                "timeout_seconds": timeout_seconds,
                "sse_read_timeout_seconds": sse_read_timeout_seconds,
                "verify_ssl": verify_ssl,
            }
        elif headers:
            self._connection_params["headers"] = validated_headers

        if not self._session_context:
            import uuid

            param_hash = uuid.uuid4().hex[:8]
            self._session_context = f"default_http_{param_hash}"

        session = await self._get_or_create_session()
        response = await session.list_tools()
        self._connected = True
        return response.tools

    async def connect_to_server(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        sse_read_timeout_seconds: int = 30,
        *,
        verify_ssl: bool = True,
    ) -> list[StructuredTool]:
        """使用 Streamable HTTP 连接 MCP 服务器（对外接口，支持 SSE 回退）。

        契约：输入 URL 与可选 Header/超时参数，输出工具列表。
        副作用：建立网络连接并创建会话。
        失败语义：超时或连接失败抛 `ValueError`。
        """
        return await asyncio.wait_for(
            self._connect_to_server(
                url, headers, sse_read_timeout_seconds=sse_read_timeout_seconds, verify_ssl=verify_ssl
            ),
            timeout=get_settings_service().settings.mcp_server_timeout,
        )

    def set_session_context(self, context_id: str):
        """设置会话上下文标识。

        契约：输入 `context_id`，用于会话复用与清理。
        副作用：覆盖当前上下文。
        失败语义：无。
        """
        self._session_context = context_id

    async def _get_or_create_session(self) -> ClientSession:
        """为当前上下文获取或创建会话。

        契约：输出可用 `ClientSession`。
        副作用：可能创建新会话并缓存。
        失败语义：缺少上下文或连接参数时抛 `ValueError`。
        """
        if not self._session_context or not self._connection_params:
            msg = "Session context and params must be set"
            raise ValueError(msg)

        session_manager = self._get_session_manager()
        self.session = await session_manager.get_session(
            self._session_context, self._connection_params, "streamable_http"
        )
        return self.session

    async def _terminate_remote_session(self) -> None:
        """尝试通过 HTTP DELETE 主动终止远端会话（尽力而为）。

        契约：无返回值，失败仅记录日志。
        副作用：向远端发送 DELETE 请求。
        失败语义：异常被捕获并记录，不影响后续清理。
        """
        if not self._connection_params or "url" not in self._connection_params:
            return

        url: str = self._connection_params["url"]

        session_id = None
        if getattr(self, "session", None) is not None:
            session_id = getattr(self.session, "session_id", None) or getattr(self.session, "id", None)

        headers: dict[str, str] = dict(self._connection_params.get("headers", {}))
        if session_id:
            headers["Mcp-Session-Id"] = str(session_id)

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.delete(url, headers=headers)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Unable to send session DELETE to '{url}': {e}")

    async def run_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """在当前会话上下文中执行工具。

        契约：输入 `tool_name/arguments`，输出工具执行结果。
        关键路径（三步）：
        1) 确保已连接并补齐默认 context。
        2) 获取/创建会话并调用工具。
        3) 失败时按错误类型重试或抛错。

        异常流：连接不可用或执行失败抛 `ValueError`。
        性能瓶颈：远程调用超时与会话重建。
        排障入口：日志关键字 `Tool '{tool_name}' failed`。
        """
        if not self._connected or not self._connection_params:
            msg = "Session not initialized or disconnected. Call connect_to_server first."
            raise ValueError(msg)

        if not self._session_context:
            import uuid

            param_hash = uuid.uuid4().hex[:8]
            self._session_context = f"default_http_{param_hash}"

        max_retries = 2
        last_error_type = None

        for attempt in range(max_retries):
            try:
                await logger.adebug(f"Attempting to run tool '{tool_name}' (attempt {attempt + 1}/{max_retries})")
                session = await self._get_or_create_session()

                result = await asyncio.wait_for(
                    session.call_tool(tool_name, arguments=arguments),
                    timeout=30.0,
                )
            except Exception as e:
                current_error_type = type(e).__name__
                await logger.awarning(f"Tool '{tool_name}' failed on attempt {attempt + 1}: {current_error_type} - {e}")

                # 注意：识别常见 MCP 连接错误类型
                try:
                    from anyio import ClosedResourceError
                    from mcp.shared.exceptions import McpError

                    is_closed_resource_error = isinstance(e, ClosedResourceError)
                    is_mcp_connection_error = isinstance(e, McpError) and "Connection closed" in str(e)
                except ImportError:
                    is_closed_resource_error = "ClosedResourceError" in str(type(e))
                    is_mcp_connection_error = "Connection closed" in str(e)

                is_timeout_error = isinstance(e, asyncio.TimeoutError | TimeoutError)

                if last_error_type == current_error_type and attempt > 0:
                    await logger.aerror(f"Repeated {current_error_type} error for tool '{tool_name}', not retrying")
                    break

                last_error_type = current_error_type

                if (is_closed_resource_error or is_mcp_connection_error) and attempt < max_retries - 1:
                    await logger.awarning(
                        f"MCP session connection issue for tool '{tool_name}', retrying with fresh session..."
                    )
                    if self._session_context:
                        session_manager = self._get_session_manager()
                        await session_manager._cleanup_session(self._session_context)
                    await asyncio.sleep(0.5)
                    continue

                if is_timeout_error and attempt < max_retries - 1:
                    await logger.awarning(f"Tool '{tool_name}' timed out, retrying...")
                    await asyncio.sleep(1.0)
                    continue

                if (
                    isinstance(e, ConnectionError | TimeoutError | OSError | ValueError)
                    or is_closed_resource_error
                    or is_mcp_connection_error
                    or is_timeout_error
                ):
                    msg = f"Failed to run tool '{tool_name}' after {attempt + 1} attempts: {e}"
                    await logger.aerror(msg)
                    if self._session_context and self._component_cache:
                        cache_key = f"mcp_session_http_{self._session_context}"
                        self._component_cache.delete(cache_key)
                    self._connected = False
                    raise ValueError(msg) from e
                raise
            else:
                await logger.adebug(f"Tool '{tool_name}' completed successfully")
                return result

        msg = f"Failed to run tool '{tool_name}': Maximum retries exceeded with repeated {last_error_type} errors"
        await logger.aerror(msg)
        raise ValueError(msg)

    async def disconnect(self):
        """断开连接并清理资源。

        契约：释放本地会话并尝试终止远端会话。
        副作用：发送 DELETE 请求并清理缓存映射。
        失败语义：远端终止失败仅记录日志。
        """
        await self._terminate_remote_session()

        if self._session_context:
            session_manager = self._get_session_manager()
            await session_manager._cleanup_session(self._session_context)

        self.session = None
        self._connection_params = None
        self._connected = False
        self._session_context = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()


# 兼容旧接口：`MCPSseClient` 作为 `MCPStreamableHttpClient` 的别名
# 新客户端同时支持 Streamable HTTP 与 SSE 自动回退
MCPSseClient = MCPStreamableHttpClient


async def update_tools(
    server_name: str,
    server_config: dict,
    mcp_stdio_client: MCPStdioClient | None = None,
    mcp_streamable_http_client: MCPStreamableHttpClient | None = None,
    mcp_sse_client: MCPStreamableHttpClient | None = None,  # 兼容旧参数
) -> tuple[str, list[StructuredTool], dict[str, StructuredTool]]:
    """根据服务配置拉取并装配工具列表。

    契约：输入服务名与配置，输出 `(mode, tool_list, tool_cache)`。
    关键路径（三步）：
    1) 校验连接参数并选择传输模式。
    2) 连接服务器获取工具清单。
    3) 构建 `StructuredTool` 并缓存。

    异常流：配置非法或连接失败抛 `ValueError`。
    性能瓶颈：远程连接与 JSON Schema 解析。
    排障入口：日志关键字 `Invalid MCP server configuration`。
    """
    if server_config is None:
        server_config = {}
    if not server_name:
        return "", [], {}
    if mcp_stdio_client is None:
        mcp_stdio_client = MCPStdioClient()

    # 注意：兼容旧参数 `mcp_sse_client`
    if mcp_streamable_http_client is None:
        mcp_streamable_http_client = mcp_sse_client if mcp_sse_client is not None else MCPStreamableHttpClient()

    # 注意：未显式指定 mode 时按配置推断
    mode = server_config.get("mode", "")
    if not mode:
        mode = "Stdio" if "command" in server_config else "Streamable_HTTP" if "url" in server_config else ""

    command = server_config.get("command", "")
    url = server_config.get("url", "")
    tools = []
    headers = _process_headers(server_config.get("headers", {}))

    try:
        await _validate_connection_params(mode, command, url)
    except ValueError as e:
        logger.error(f"Invalid MCP server configuration for '{server_name}': {e}")
        raise

    client: MCPStdioClient | MCPStreamableHttpClient | None = None
    if mode == "Stdio":
        args = server_config.get("args", [])
        env = server_config.get("env", {})
        full_command = " ".join([command, *args])
        tools = await mcp_stdio_client.connect_to_server(full_command, env)
        client = mcp_stdio_client
    elif mode in ["Streamable_HTTP", "SSE"]:
        verify_ssl = server_config.get("verify_ssl", True)
        tools = await mcp_streamable_http_client.connect_to_server(url, headers=headers, verify_ssl=verify_ssl)
        client = mcp_streamable_http_client
    else:
        logger.error(f"Invalid MCP server mode for '{server_name}': {mode}")
        return "", [], {}

    if not tools or not client or not client._connected:
        logger.warning(f"No tools available from MCP server '{server_name}' or connection failed")
        return "", [], {}

    tool_list = []
    tool_cache: dict[str, StructuredTool] = {}
    for tool in tools:
        if not tool or not hasattr(tool, "name"):
            continue
        try:
            args_schema = create_input_schema_from_json_schema(tool.inputSchema)
            if not args_schema:
                logger.warning(f"Could not create schema for tool '{tool.name}' from server '{server_name}'")
                continue

            # 注意：自定义 StructuredTool 以处理参数命名转换
            class MCPStructuredTool(StructuredTool):
                def run(self, tool_input: str | dict, config=None, **kwargs):
                    """同步执行时在校验前转换参数命名。"""
                    # 注意：字符串输入先解析为 JSON
                    if isinstance(tool_input, str):
                        try:
                            parsed_input = json.loads(tool_input)
                        except json.JSONDecodeError:
                            parsed_input = {"input": tool_input}
                    else:
                        parsed_input = tool_input or {}

                    # 注意：将 camelCase 转为 snake_case
                    converted_input = self._convert_parameters(parsed_input)

                    return super().run(converted_input, config=config, **kwargs)

                async def arun(self, tool_input: str | dict, config=None, **kwargs):
                    """异步执行时在校验前转换参数命名。"""
                    # 注意：字符串输入先解析为 JSON
                    if isinstance(tool_input, str):
                        try:
                            parsed_input = json.loads(tool_input)
                        except json.JSONDecodeError:
                            parsed_input = {"input": tool_input}
                    else:
                        parsed_input = tool_input or {}

                    # 注意：将 camelCase 转为 snake_case
                    converted_input = self._convert_parameters(parsed_input)

                    return await super().arun(converted_input, config=config, **kwargs)

                def _convert_parameters(self, input_dict):
                    if not input_dict or not isinstance(input_dict, dict):
                        return input_dict

                    converted_dict = {}
                    original_fields = set(self.args_schema.model_fields.keys())

                    for key, value in input_dict.items():
                        if key in original_fields:
                            converted_dict[key] = value
                        else:
                            snake_key = _camel_to_snake(key)
                            if snake_key in original_fields:
                                converted_dict[snake_key] = value
                            else:
                                converted_dict[key] = value

                    return converted_dict

            tool_obj = MCPStructuredTool(
                name=tool.name,
                description=tool.description or "",
                args_schema=args_schema,
                func=create_tool_func(tool.name, args_schema, client),
                coroutine=create_tool_coroutine(tool.name, args_schema, client),
                tags=[tool.name],
                metadata={"server_name": server_name},
            )

            tool_list.append(tool_obj)
            tool_cache[tool.name] = tool_obj
        except (ConnectionError, TimeoutError, OSError, ValueError) as e:
            logger.error(f"Failed to create tool '{tool.name}' from server '{server_name}': {e}")
            msg = f"Failed to create tool '{tool.name}' from server '{server_name}': {e}"
            raise ValueError(msg) from e

    logger.info(f"Successfully loaded {len(tool_list)} tools from MCP server '{server_name}'")
    return mode, tool_list, tool_cache
