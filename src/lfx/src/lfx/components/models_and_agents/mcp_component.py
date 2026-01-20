"""
模块名称：MCP 工具组件

本模块封装与 MCP Server 的交互逻辑，支持工具列表拉取、缓存与动态输入生成。
主要功能：
- 解析 MCP Server 配置并加载工具列表；
- 按工具 schema 动态生成输入项；
- 执行工具并返回结构化输出。

关键组件：
- MCPToolsComponent：MCP 工具组件入口。

设计背景：统一 MCP Server 的接入与工具执行流程，避免在节点层重复实现。
注意事项：缓存启用会影响配置更新生效时机，必要时关闭 `use_cache`。
"""

from __future__ import annotations

import asyncio
import json
import uuid

from langchain_core.tools import StructuredTool  # noqa: TC002

from lfx.base.agents.utils import maybe_unflatten_dict, safe_cache_get, safe_cache_set
from lfx.base.mcp.util import (
    MCPStdioClient,
    MCPStreamableHttpClient,
    create_input_schema_from_json_schema,
    update_tools,
)
from lfx.custom.custom_component.component_with_cache import ComponentWithCache
from lfx.inputs.inputs import InputTypes  # noqa: TC001
from lfx.io import BoolInput, DictInput, DropdownInput, McpInput, MessageTextInput, Output
from lfx.io.schema import flatten_schema, schema_to_langflow_inputs
from lfx.log.logger import logger
from lfx.schema.dataframe import DataFrame
from lfx.schema.message import Message
from lfx.services.deps import get_settings_service, get_storage_service, session_scope


def resolve_mcp_config(
    server_name: str,  # noqa: ARG001
    server_config_from_value: dict | None,
    server_config_from_db: dict | None,
) -> dict | None:
    """解析 MCP Server 配置并处理优先级

    契约：优先使用数据库配置，其次使用传入配置；两者为空返回 None。
    关键路径：1) 读取 DB 配置 2) 回退到 value 配置。
    决策：数据库配置优先于前端传入
    问题：编辑后的配置必须即时生效
    方案：以 DB 为权威来源
    代价：DB 不可用时只能回退
    重评：当配置完全由 API 侧控制时
    """
    if server_config_from_db:
        return server_config_from_db
    return server_config_from_value


class MCPToolsComponent(ComponentWithCache):
    """MCP 工具组件封装

    契约：依赖 MCP Server 配置与工具列表；输出为工具执行结果 `DataFrame`。
    关键路径：1) 解析/缓存 MCP Server 配置 2) 拉取工具 schema 3) 执行工具并返回输出。
    决策：提供可选缓存以减少重复拉取
    问题：工具列表获取成本高且频繁
    方案：用组件缓存复用工具列表
    代价：配置更新可能延迟生效
    重评：当 MCP Server 变更频率高时
    """
    schema_inputs: list = []
    tools: list[StructuredTool] = []
    _not_load_actions: bool = False
    _tool_cache: dict = {}
    _last_selected_server: str | None = None  # 注意：缓存最近选择的服务器以减少重复刷新。

    def __init__(self, **data) -> None:
        """初始化 MCP 组件并准备缓存结构

        契约：构造后保证缓存键存在，并初始化 stdio/http 客户端。
        关键路径：1) 初始化共享缓存结构 2) 创建客户端实例。
        """
        super().__init__(**data)
        # 注意：预置缓存键以避免 CacheMiss。
        self._ensure_cache_structure()

        # 实现：客户端复用组件缓存以共享会话/工具状态。
        self.stdio_client: MCPStdioClient = MCPStdioClient(component_cache=self._shared_component_cache)
        self.streamable_http_client: MCPStreamableHttpClient = MCPStreamableHttpClient(
            component_cache=self._shared_component_cache
        )

    def _ensure_cache_structure(self):
        """确保组件缓存具备必要键

        契约：保证 `servers` 与 `last_selected_server` 键存在。
        关键路径：1) 检查键是否存在 2) 缺失则写入默认值。
        """
        # 注意：缺失键会导致后续访问失败。
        servers_value = safe_cache_get(self._shared_component_cache, "servers")
        if servers_value is None:
            safe_cache_set(self._shared_component_cache, "servers", {})

        # 注意：为空字符串表示尚未选择服务器。
        last_server_value = safe_cache_get(self._shared_component_cache, "last_selected_server")
        if last_server_value is None:
            safe_cache_set(self._shared_component_cache, "last_selected_server", "")

    default_keys: list[str] = [
        "code",
        "_type",
        "tool_mode",
        "tool_placeholder",
        "mcp_server",
        "tool",
        "use_cache",
        "verify_ssl",
        "headers",
    ]

    display_name = "MCP Tools"
    description = "Connect to an MCP server to use its tools."
    documentation: str = "https://docs.langflow.org/mcp-tools"
    icon = "Mcp"
    name = "MCPTools"

    inputs = [
        McpInput(
            name="mcp_server",
            display_name="MCP Server",
            info="Select the MCP Server that will be used by this component",
            real_time_refresh=True,
        ),
        BoolInput(
            name="use_cache",
            display_name="Use Cached Server",
            info=(
                "Enable caching of MCP Server and tools to improve performance. "
                "Disable to always fetch fresh tools and server updates."
            ),
            value=False,
            advanced=True,
        ),
        BoolInput(
            name="verify_ssl",
            display_name="Verify SSL Certificate",
            info=(
                "Enable SSL certificate verification for HTTPS connections. "
                "Disable only for development/testing with self-signed certificates."
            ),
            value=True,
            advanced=True,
        ),
        DictInput(
            name="headers",
            display_name="Headers",
            info=(
                "HTTP headers to include with MCP server requests. "
                "Useful for authentication (e.g., Authorization header). "
                "These headers override any headers configured in the MCP server settings."
            ),
            advanced=True,
            is_list=True,
        ),
        DropdownInput(
            name="tool",
            display_name="Tool",
            options=[],
            value="",
            info="Select the tool to execute",
            show=False,
            required=True,
            real_time_refresh=True,
        ),
        MessageTextInput(
            name="tool_placeholder",
            display_name="Tool Placeholder",
            info="Placeholder for the tool",
            value="",
            show=False,
            tool_mode=False,
        ),
    ]

    outputs = [
        Output(display_name="Response", name="response", method="build_output"),
    ]

    async def _validate_schema_inputs(self, tool_obj) -> list[InputTypes]:
        """校验并生成工具输入字段

        契约：返回 `InputTypes` 列表；无有效参数时返回空列表。
        关键路径：1) 展平 schema 2) 生成输入 schema 3) 转换为 Langflow 输入。
        异常流：schema 异常时抛 `ValueError`。
        排障入口：日志 `Error validating schema inputs`。
        """
        try:
            if not tool_obj or not hasattr(tool_obj, "args_schema"):
                msg = "Invalid tool object or missing input schema"
                raise ValueError(msg)

            flat_schema = flatten_schema(tool_obj.args_schema.schema())
            input_schema = create_input_schema_from_json_schema(flat_schema)
            if not input_schema:
                msg = f"Empty input schema for tool '{tool_obj.name}'"
                raise ValueError(msg)

            schema_inputs = schema_to_langflow_inputs(input_schema)
            if not schema_inputs:
                msg = f"No input parameters defined for tool '{tool_obj.name}'"
                await logger.awarning(msg)
                return []

        except Exception as e:
            msg = f"Error validating schema inputs: {e!s}"
            await logger.aexception(msg)
            raise ValueError(msg) from e
        else:
            return schema_inputs

    async def update_tool_list(self, mcp_server_value=None):
        """加载或刷新 MCP 工具列表

        契约：返回 `(tools, server_info)`；失败抛 `ValueError/TimeoutError`。
        关键路径（三步）：
        1) 解析 server_name 与缓存策略
        2) 优先使用缓存/数据库配置
        3) 拉取工具并更新缓存
        异常流：超时抛 `TimeoutError`，其他异常抛 `ValueError`。
        决策：数据库配置优先并可被缓存
        问题：编辑配置需要及时生效但又要减少拉取成本
        方案：DB 优先 + 可选缓存
        代价：缓存可能导致延迟更新
        重评：当配置变更频繁且必须实时生效时
        """
        # 实现：允许传入 {name, config}，否则使用组件字段。
        mcp_server = mcp_server_value if mcp_server_value is not None else getattr(self, "mcp_server", None)
        server_name = None
        server_config_from_value = None
        if isinstance(mcp_server, dict):
            server_name = mcp_server.get("name")
            server_config_from_value = mcp_server.get("config")
        else:
            server_name = mcp_server
        if not server_name:
            self.tools = []
            return [], {"name": server_name, "config": server_config_from_value}

        # 注意：缓存默认关闭，避免配置更新不生效。
        use_cache = getattr(self, "use_cache", False)

        # 实现：启用缓存时优先读取共享缓存。
        cached = None
        if use_cache:
            servers_cache = safe_cache_get(self._shared_component_cache, "servers", {})
            cached = servers_cache.get(server_name) if isinstance(servers_cache, dict) else None

        if cached is not None:
            try:
                self.tools = cached["tools"]
                self.tool_names = cached["tool_names"]
                self._tool_cache = cached["tool_cache"]
                server_config_from_value = cached["config"]
            except (TypeError, KeyError, AttributeError) as e:
                # 排障：缓存损坏时清除并回退到重新拉取。
                msg = f"Unable to use cached data for MCP Server{server_name}: {e}"
                await logger.awarning(msg)
                # 注意：仅清除当前 server 的缓存，避免影响其他服务。
                current_servers_cache = safe_cache_get(self._shared_component_cache, "servers", {})
                if isinstance(current_servers_cache, dict) and server_name in current_servers_cache:
                    current_servers_cache.pop(server_name)
                    safe_cache_set(self._shared_component_cache, "servers", current_servers_cache)
            else:
                return self.tools, {"name": server_name, "config": server_config_from_value}

        try:
            # 决策：优先从数据库获取，确保编辑生效。
            try:
                from langflow.api.v2.mcp import get_server
                from langflow.services.database.models.user.crud import get_user_by_id
            except ImportError as e:
                msg = (
                    "Langflow MCP server functionality is not available. "
                    "This feature requires the full Langflow installation."
                )
                raise ImportError(msg) from e

            server_config_from_db = None
            async with session_scope() as db:
                if not self.user_id:
                    msg = "User ID is required for fetching MCP tools."
                    raise ValueError(msg)
                current_user = await get_user_by_id(db, self.user_id)

                # 实现：尝试从 DB/API 获取服务器配置。
                server_config_from_db = await get_server(
                    server_name,
                    current_user,
                    db,
                    storage_service=get_storage_service(),
                    settings_service=get_settings_service(),
                )

            # 实现：按优先级合并配置。
            server_config = resolve_mcp_config(
                server_name=server_name,
                server_config_from_value=server_config_from_value,
                server_config_from_db=server_config_from_db,
            )

            if not server_config:
                self.tools = []
                return [], {"name": server_name, "config": server_config}

            # 注意：未配置时补充 verify_ssl，避免隐式默认不一致。
            if "verify_ssl" not in server_config:
                verify_ssl = getattr(self, "verify_ssl", True)
                server_config["verify_ssl"] = verify_ssl

            # 实现：组件 headers 优先于服务端配置 headers。
            component_headers = getattr(self, "headers", None) or []
            if component_headers:
                # 注意：支持 list/dict 两种 headers 输入格式。
                component_headers_dict = {}
                if isinstance(component_headers, list):
                    for item in component_headers:
                        if isinstance(item, dict) and "key" in item and "value" in item:
                            component_headers_dict[item["key"]] = item["value"]
                elif isinstance(component_headers, dict):
                    component_headers_dict = component_headers

                if component_headers_dict:
                    existing_headers = server_config.get("headers", {}) or {}
                    # 注意：服务端 headers 可能为 list，需先归一化为 dict。
                    if isinstance(existing_headers, list):
                        existing_dict = {}
                        for item in existing_headers:
                            if isinstance(item, dict) and "key" in item and "value" in item:
                                existing_dict[item["key"]] = item["value"]
                        existing_headers = existing_dict
                    merged_headers = {**existing_headers, **component_headers_dict}
                    server_config["headers"] = merged_headers

            _, tool_list, tool_cache = await update_tools(
                server_name=server_name,
                server_config=server_config,
                mcp_stdio_client=self.stdio_client,
                mcp_streamable_http_client=self.streamable_http_client,
            )

            self.tool_names = [tool.name for tool in tool_list if hasattr(tool, "name")]
            self._tool_cache = tool_cache
            self.tools = tool_list

            # 注意：仅在启用缓存时写入缓存。
            if use_cache:
                cache_data = {
                    "tools": tool_list,
                    "tool_names": self.tool_names,
                    "tool_cache": tool_cache,
                    "config": server_config,
                }

                # 实现：安全写入共享缓存。
                current_servers_cache = safe_cache_get(self._shared_component_cache, "servers", {})
                if isinstance(current_servers_cache, dict):
                    current_servers_cache[server_name] = cache_data
                    safe_cache_set(self._shared_component_cache, "servers", current_servers_cache)

        except (TimeoutError, asyncio.TimeoutError) as e:
            msg = f"Timeout updating tool list: {e!s}"
            await logger.aexception(msg)
            raise TimeoutError(msg) from e
        except Exception as e:
            msg = f"Error updating tool list: {e!s}"
            await logger.aexception(msg)
            raise ValueError(msg) from e
        else:
            return tool_list, {"name": server_name, "config": server_config}

    async def update_build_config(self, build_config: dict, field_value: str, field_name: str | None = None) -> dict:
        """更新构建配置并切换工具/服务器相关字段

        契约：返回更新后的 `build_config`；必要时会刷新工具列表。
        关键路径（三步）：
        1) 根据变更字段刷新工具列表或清理配置
        2) 处理缓存/工具模式逻辑
        3) 更新工具输入与占位状态
        异常流：更新失败抛 `ValueError`。
        排障入口：日志 `Error in update_build_config`。
        决策：在构建阶段处理工具刷新
        问题：工具列表依赖外部服务器变化
        方案：基于字段变更触发刷新与缓存
        代价：构建阶段可能触发网络请求
        重评：当支持后台异步刷新时
        """
        try:
            if field_name == "tool":
                try:
                    # 注意：未启用缓存或工具为空时强制刷新，确保配置变更生效。
                    use_cache = getattr(self, "use_cache", False)
                    if len(self.tools) == 0 or not use_cache:
                        try:
                            self.tools, build_config["mcp_server"]["value"] = await self.update_tool_list()
                            build_config["tool"]["options"] = [tool.name for tool in self.tools]
                            build_config["tool"]["placeholder"] = "Select a tool"
                        except (TimeoutError, asyncio.TimeoutError) as e:
                            msg = f"Timeout updating tool list: {e!s}"
                            await logger.aexception(msg)
                            if not build_config["tools_metadata"]["show"]:
                                build_config["tool"]["show"] = True
                                build_config["tool"]["options"] = []
                                build_config["tool"]["value"] = ""
                                build_config["tool"]["placeholder"] = "Timeout on MCP server"
                            else:
                                build_config["tool"]["show"] = False
                        except ValueError:
                            if not build_config["tools_metadata"]["show"]:
                                build_config["tool"]["show"] = True
                                build_config["tool"]["options"] = []
                                build_config["tool"]["value"] = ""
                                build_config["tool"]["placeholder"] = "Error on MCP Server"
                            else:
                                build_config["tool"]["show"] = False

                    if field_value == "":
                        return build_config
                    tool_obj = None
                    for tool in self.tools:
                        if tool.name == field_value:
                            tool_obj = tool
                            break
                    if tool_obj is None:
                        msg = f"Tool {field_value} not found in available tools: {self.tools}"
                        await logger.awarning(msg)
                        return build_config
                    await self._update_tool_config(build_config, field_value)
                except Exception as e:
                    build_config["tool"]["options"] = []
                    msg = f"Failed to update tools: {e!s}"
                    raise ValueError(msg) from e
                else:
                    return build_config
            elif field_name == "mcp_server":
                if not field_value:
                    build_config["tool"]["show"] = False
                    build_config["tool"]["options"] = []
                    build_config["tool"]["value"] = ""
                    build_config["tool"]["placeholder"] = ""
                    build_config["tool_placeholder"]["tool_mode"] = False
                    self.remove_non_default_keys(build_config)
                    return build_config

                build_config["tool_placeholder"]["tool_mode"] = True

                current_server_name = field_value.get("name") if isinstance(field_value, dict) else field_value
                _last_selected_server = safe_cache_get(self._shared_component_cache, "last_selected_server", "")
                server_changed = current_server_name != _last_selected_server

                # 注意：tool_mode 启用时隐藏下拉框并以 placeholder 方式传参。
                is_in_tool_mode = build_config["tools_metadata"]["show"]

                # 实现：按 use_cache 决定是否走缓存路径。
                use_cache = getattr(self, "use_cache", False)

                # 注意：若服务未变且已有 options，可在缓存开启或工具模式下走快速路径。
                existing_options = build_config.get("tool", {}).get("options") or []
                if not server_changed and existing_options:
                    # 注意：非工具模式且禁用缓存时必须刷新。
                    if not is_in_tool_mode and not use_cache:
                        pass  # 注意：继续执行刷新逻辑。
                    else:
                        if not is_in_tool_mode:
                            build_config["tool"]["show"] = True
                        return build_config

                # 注意：仅当服务变更或禁用缓存时刷新，避免频繁请求。
                if (_last_selected_server in (current_server_name, "")) and build_config["tool"]["show"] and use_cache:
                    if current_server_name:
                        servers_cache = safe_cache_get(self._shared_component_cache, "servers", {})
                        if isinstance(servers_cache, dict):
                            cached = servers_cache.get(current_server_name)
                            if cached is not None and cached.get("tool_names"):
                                cached_tools = cached["tool_names"]
                                current_tools = build_config["tool"]["options"]
                                if current_tools == cached_tools:
                                    return build_config
                    else:
                        return build_config
                safe_cache_set(self._shared_component_cache, "last_selected_server", current_server_name)

                # 注意：关闭缓存时清理当前服务缓存，强制拉取最新数据。
                if not use_cache and current_server_name:
                    servers_cache = safe_cache_get(self._shared_component_cache, "servers", {})
                    if isinstance(servers_cache, dict) and current_server_name in servers_cache:
                        servers_cache.pop(current_server_name)
                        safe_cache_set(self._shared_component_cache, "servers", servers_cache)

                # 实现：启用缓存时先读取已缓存工具。
                cached_tools = None
                if current_server_name and use_cache:
                    servers_cache = safe_cache_get(self._shared_component_cache, "servers", {})
                    if isinstance(servers_cache, dict):
                        cached = servers_cache.get(current_server_name)
                        if cached is not None:
                            try:
                                cached_tools = cached["tools"]
                                self.tools = cached_tools
                                self.tool_names = cached["tool_names"]
                                self._tool_cache = cached["tool_cache"]
                            except (TypeError, KeyError, AttributeError) as e:
                                # 排障：缓存损坏时忽略并回退刷新。
                                msg = f"Unable to use cached data for MCP Server,{current_server_name}: {e}"
                                await logger.awarning(msg)
                                cached_tools = None

                # 注意：禁用缓存或无缓存时清空工具列表以触发刷新。
                if not cached_tools or not use_cache:
                    self.tools = []  # 注意：清空旧工具以强制刷新。

                # 注意：服务变更或禁用缓存时清理旧工具输入。
                if server_changed or not use_cache:
                    self.remove_non_default_keys(build_config)

                # 注意：非 tool_mode 才显示工具下拉框。
                if not is_in_tool_mode:
                    build_config["tool"]["show"] = True
                    if cached_tools:
                        # 实现：使用缓存工具立即填充 options。
                        build_config["tool"]["options"] = [tool.name for tool in cached_tools]
                        build_config["tool"]["placeholder"] = "Select a tool"
                    else:
                        # 实现：需要拉取时展示加载态。
                        build_config["tool"]["placeholder"] = "Loading tools..."
                        build_config["tool"]["options"] = []
                    # 注意：服务变更/无缓存/禁用缓存时强制刷新值触发重渲染。
                    if server_changed or not cached_tools or not use_cache:
                        build_config["tool"]["value"] = uuid.uuid4()
                else:
                    # 注意：工具模式下保持隐藏，避免与工具输入冲突。
                    self._not_load_actions = True
                    build_config["tool"]["show"] = False

            elif field_name == "tool_mode":
                build_config["tool"]["placeholder"] = ""
                build_config["tool"]["show"] = not bool(field_value) and bool(build_config["mcp_server"])
                self.remove_non_default_keys(build_config)
                self.tool = build_config["tool"]["value"]
                if field_value:
                    self._not_load_actions = True
                else:
                    build_config["tool"]["value"] = uuid.uuid4()
                    build_config["tool"]["options"] = []
                    build_config["tool"]["show"] = True
                    build_config["tool"]["placeholder"] = "Loading tools..."
            elif field_name == "tools_metadata":
                self._not_load_actions = False

        except Exception as e:
            msg = f"Error in update_build_config: {e!s}"
            await logger.aexception(msg)
            raise ValueError(msg) from e
        else:
            return build_config

    def get_inputs_for_all_tools(self, tools: list) -> dict:
        """获取所有工具的输入字段定义

        契约：返回 `{tool_name: inputs}` 字典；忽略无效工具。
        关键路径：1) 展平 schema 2) 生成输入定义 3) 汇总返回。
        异常流：单个工具解析失败只记录日志，不影响其他工具。
        决策：失败隔离而非整体失败
        问题：单工具 schema 异常不应影响其他工具
        方案：捕获异常并跳过该工具
        代价：可能隐藏部分错误
        重评：当需要强一致性时
        """
        inputs = {}
        for tool in tools:
            if not tool or not hasattr(tool, "name"):
                continue
            try:
                flat_schema = flatten_schema(tool.args_schema.schema())
                input_schema = create_input_schema_from_json_schema(flat_schema)
                langflow_inputs = schema_to_langflow_inputs(input_schema)
                inputs[tool.name] = langflow_inputs
            except (AttributeError, ValueError, TypeError, KeyError) as e:
                msg = f"Error getting inputs for tool {getattr(tool, 'name', 'unknown')}: {e!s}"
                logger.exception(msg)
                continue
        return inputs

    def remove_non_default_keys(self, build_config: dict) -> None:
        """移除非默认字段以清理动态输入。"""
        for key in list(build_config.keys()):
            if key not in self.default_keys:
                build_config.pop(key)

    async def _update_tool_config(self, build_config: dict, tool_name: str) -> None:
        """更新选中工具的动态输入

        契约：根据 `tool_name` 重建动态输入并尽量保留原值。
        关键路径：1) 获取工具对象 2) 清理旧输入 3) 添加新输入并回填值。
        异常流：schema 校验失败时清空 `schema_inputs` 并返回。
        排障入口：日志 `Schema validation error for tool`。
        """
        if not self.tools:
            self.tools, build_config["mcp_server"]["value"] = await self.update_tool_list()

        if not tool_name:
            return

        tool_obj = next((tool for tool in self.tools if tool.name == tool_name), None)
        if not tool_obj:
            msg = f"Tool {tool_name} not found in available tools: {self.tools}"
            self.remove_non_default_keys(build_config)
            build_config["tool"]["value"] = ""
            await logger.awarning(msg)
            return

        try:
            # 注意：先缓存当前值，避免切换后丢失用户输入。
            current_values = {}
            for key, value in build_config.items():
                if key not in self.default_keys and isinstance(value, dict) and "value" in value:
                    current_values[key] = value["value"]

            # 注意：清空旧工具输入，避免残留字段误提交。
            self.remove_non_default_keys(build_config)

            # 实现：按新工具 schema 生成输入列表。
            self.schema_inputs = await self._validate_schema_inputs(tool_obj)
            if not self.schema_inputs:
                msg = f"No input parameters to configure for tool '{tool_name}'"
                await logger.ainfo(msg)
                return

            # 实现：仅为当前工具添加输入字段。
            for schema_input in self.schema_inputs:
                if not schema_input or not hasattr(schema_input, "name"):
                    msg = "Invalid schema input detected, skipping"
                    await logger.awarning(msg)
                    continue

                try:
                    name = schema_input.name
                    input_dict = schema_input.to_dict()
                    input_dict.setdefault("value", None)
                    input_dict.setdefault("required", True)

                    build_config[name] = input_dict

                    # 注意：若字段名相同则保留历史值。
                    if name in current_values:
                        build_config[name]["value"] = current_values[name]

                except (AttributeError, KeyError, TypeError) as e:
                    msg = f"Error processing schema input {schema_input}: {e!s}"
                    await logger.aexception(msg)
                    continue
        except ValueError as e:
            msg = f"Schema validation error for tool {tool_name}: {e!s}"
            await logger.aexception(msg)
            self.schema_inputs = []
            return
        except (AttributeError, KeyError, TypeError) as e:
            msg = f"Error updating tool config: {e!s}"
            await logger.aexception(msg)
            raise ValueError(msg) from e

    async def build_output(self) -> DataFrame:
        """执行 MCP 工具并返回输出

        契约：返回 `DataFrame`；未选择工具时返回错误行。
        关键路径（三步）：
        1) 更新工具列表与会话上下文
        2) 组装工具参数并执行
        3) 格式化输出为 DataFrame
        异常流：执行失败抛 `ValueError`。
        排障入口：日志 `Error in build_output`。
        决策：统一输出为 DataFrame
        问题：工具返回体结构不一致
        方案：在组件内规范化为 DataFrame
        代价：复杂嵌套结构可能被扁平化或包装
        重评：当下游可直接消费原始结构时
        """
        try:
            self.tools, _ = await self.update_tool_list()
            if self.tool != "":
                # 注意：为可复用会话设置 session_context，减少 MCP 重建成本。
                session_context = self._get_session_context()
                if session_context:
                    self.stdio_client.set_session_context(session_context)
                    self.streamable_http_client.set_session_context(session_context)
                exec_tool = self._tool_cache[self.tool]
                tool_args = self.get_inputs_for_all_tools(self.tools)[self.tool]
                kwargs = {}
                for arg in tool_args:
                    value = getattr(self, arg.name, None)
                    if value is not None:
                        if isinstance(value, Message):
                            kwargs[arg.name] = value.text
                        else:
                            kwargs[arg.name] = value

                unflattened_kwargs = maybe_unflatten_dict(kwargs)

                output = await exec_tool.coroutine(**unflattened_kwargs)
                tool_content = []
                for item in output.content:
                    item_dict = item.model_dump()
                    item_dict = self.process_output_item(item_dict)
                    tool_content.append(item_dict)

                if isinstance(tool_content, list) and all(isinstance(x, dict) for x in tool_content):
                    return DataFrame(tool_content)
                return DataFrame(data=tool_content)
            return DataFrame(data=[{"error": "You must select a tool"}])
        except Exception as e:
            msg = f"Error in build_output: {e!s}"
            await logger.aexception(msg)
            raise ValueError(msg) from e

    def process_output_item(self, item_dict):
        """规范化单条工具输出

        契约：输入 dict，返回可序列化 dict。
        关键路径：1) 尝试解析 text 为 JSON 2) 包装非 dict 结果。
        """
        if item_dict.get("type") == "text":
            text = item_dict.get("text")
            try:
                parsed = json.loads(text)
                # 注意：DataFrame 期望 dict 结构，非 dict 需包装。
                if isinstance(parsed, dict):
                    return parsed
                # 注意：保持原文与解析值，便于排障。
                return {"text": text, "parsed_value": parsed, "type": "text"}  # noqa: TRY300
            except json.JSONDecodeError:
                return item_dict
        return item_dict

    def _get_session_context(self) -> str | None:
        """生成 MCP 会话上下文标识

        契约：返回 `session_id_server` 或 None。
        关键路径：1) 读取 graph.session_id 2) 拼接 server_name。
        """
        # 注意：优先使用图执行上下文中的 session_id。
        if hasattr(self, "graph") and hasattr(self.graph, "session_id"):
            session_id = self.graph.session_id
            # 注意：拼接 server_name 以隔离不同 MCP Server 会话。
            server_name = ""
            mcp_server = getattr(self, "mcp_server", None)
            if isinstance(mcp_server, dict):
                server_name = mcp_server.get("name", "")
            elif mcp_server:
                server_name = str(mcp_server)
            return f"{session_id}_{server_name}" if session_id else None
        return None

    async def _get_tools(self):
        """获取工具列表（必要时刷新）。"""
        mcp_server = getattr(self, "mcp_server", None)
        if not self._not_load_actions:
            tools, _ = await self.update_tool_list(mcp_server)
            return tools
        return []
