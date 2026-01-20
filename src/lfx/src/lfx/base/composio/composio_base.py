"""
模块名称：Composio 基础组件

本模块提供 Composio 组件的通用能力封装，负责将 Composio SDK 的工具与 schema
映射为 Langflow 输入/输出配置，支撑 UI 与运行时之间的桥接。主要功能包括：
- 拉取工具清单与参数 schema，并生成可渲染的输入字段
- 处理连接/认证流程（托管与自定义模式）
- 执行动作并做结果后处理/结构转换

关键组件：
- ComposioBaseComponent：动作加载、认证 UI、执行与缓存
- _populate_actions_data/_validate_schema_inputs：schema 解析与字段转换
- update_build_config：驱动 UI 状态机与连接流程

设计背景：Composio 工具模型与 Langflow 字段模型不一致，需要集中适配与缓存
注意事项：Astra 云环境禁用；schema 解析可能较慢，依赖缓存与错误兜底
"""

import copy
import json
import re
from contextlib import suppress
from typing import Any

from composio import Composio
from composio_langchain import LangchainProvider
from langchain_core.tools import Tool

from lfx.base.mcp.util import create_input_schema_from_json_schema
from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import (
    AuthInput,
    DropdownInput,
    FileInput,
    InputTypes,
    MessageTextInput,
    MultilineInput,
    SecretStrInput,
    SortableListInput,
    StrInput,
    TabInput,
)
from lfx.io import Output
from lfx.io.schema import flatten_schema, schema_to_langflow_inputs
from lfx.log.logger import logger
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame
from lfx.schema.message import Message
from lfx.utils.validate_cloud import raise_error_if_astra_cloud_disable_component

disable_component_in_astra_cloud_msg = (
    "Composio tools are not supported in Astra cloud environment. "
    "Please use local storage mode or cloud-based versions of the tools."
)


class ComposioBaseComponent(Component):
    """Composio 组件基础类，统一动作加载、认证流程与执行路径。
    契约：输入 `api_key`/`entity_id`/`action_button`；输出 `Message`/`DataFrame`/`Data`；副作用为调用 Composio API、更新 build_config、读写类级缓存；失败语义为 API Key 缺失或 SDK 异常抛 `ValueError`。
    关键路径：1) 拉取工具与 schema 并缓存 2) 生成输入字段与 UI 状态 3) 执行动作并后处理。
    决策：集中在基类做 SDK 适配。问题：多组件重复实现且易漂移；方案：基类统一；代价：耦合集中；重评：当工具集差异过大或复用受限时。
    排障入口：日志关键字 `Composio` / `actions` / `auth_link`。
    """

    default_tools_limit: int = 5

    # 注意：以下名称与 Component 基类属性冲突，字段映射时必须避免覆盖。
    RESERVED_ATTRIBUTES: set[str] = {
        # 注意：组件核心字段名。
        "name",
        "description",
        "status",
        "display_name",
        "icon",
        "priority",
        "code",
        "inputs",
        "outputs",
        "selected_output",
        # 注意：常见属性与方法名。
        "trace_type",
        "trace_name",
        "function",
        "repr_value",
        "field_config",
        "field_order",
        "frozen",
        "build_parameters",
        "cache",
        "tools_metadata",
        "vertex",
        # 注意：用户与会话维度的属性名。
        "user_id",  # 注意：已有专门处理，但为完整性保留。
        "session_id",
        "flow_id",
        "flow_name",
        "context",
        # 注意：常见方法名，避免与组件方法冲突。
        "build",
        "run",
        "stop",
        "start",
        "validate",
        "get_function",
        "set_attributes",
        # 注意：其他高频冲突名。
        "id",
        "type",
        "value",
        "metadata",
        "logs",
        "results",
        "artifacts",
        "parameters",
        "template",
        "config",
    }

    _base_inputs = [
        MessageTextInput(
            name="entity_id",
            display_name="Entity ID",
            value="default",
            advanced=True,
            tool_mode=True,
        ),
        SecretStrInput(
            name="api_key",
            display_name="Composio API Key",
            required=True,
            real_time_refresh=True,
            value="COMPOSIO_API_KEY",
        ),
        DropdownInput(
            name="auth_mode",
            display_name="Auth Mode",
            options=[],
            placeholder="Select auth mode",
            toggle=True,
            toggle_disable=True,
            show=False,
            real_time_refresh=True,
            helper_text="Choose how to authenticate with the toolkit.",
        ),
        AuthInput(
            name="auth_link",
            value="",
            auth_tooltip="Please insert a valid Composio API Key.",
            show=False,
        ),
        # 注意：动态认证占位字段，默认隐藏。
        SecretStrInput(
            name="client_id",
            display_name="Client ID",
            info="",
            show=False,
            value="",
            required=False,
            real_time_refresh=True,
        ),
        SecretStrInput(
            name="client_secret",
            display_name="Client Secret",
            info="",
            show=False,
            value="",
            required=False,
            real_time_refresh=True,
        ),
        StrInput(
            name="verification_token",
            display_name="Verification Token",
            info="",
            show=False,
            value="",
            required=False,
            real_time_refresh=True,
        ),
        StrInput(
            name="redirect_uri",
            display_name="Redirect URI",
            info="",
            show=False,
            value="",
            required=False,
            real_time_refresh=True,
        ),
        StrInput(
            name="authorization_url",
            display_name="Authorization URL",
            info="",
            show=False,
            value="",
            required=False,
            real_time_refresh=True,
        ),
        StrInput(
            name="token_url",
            display_name="Token URL",
            info="",
            show=False,
            value="",
            required=False,
            real_time_refresh=True,
        ),
        # 注意：API Key 认证字段。
        SecretStrInput(
            name="api_key_field",
            display_name="API Key",
            info="",
            show=False,
            value="",
            required=False,
            real_time_refresh=True,
        ),
        SecretStrInput(
            name="generic_api_key",
            display_name="API Key",
            info="Enter API key on Composio page",
            show=False,
            value="",
            required=False,
            real_time_refresh=True,
        ),
        SecretStrInput(
            name="token",
            display_name="Token",
            info="",
            show=False,
            value="",
            required=False,
            real_time_refresh=True,
        ),
        SecretStrInput(
            name="access_token",
            display_name="Access Token",
            info="",
            show=False,
            value="",
            required=False,
            real_time_refresh=True,
        ),
        SecretStrInput(
            name="refresh_token",
            display_name="Refresh Token",
            info="",
            show=False,
            value="",
            required=False,
            real_time_refresh=True,
        ),
        # 注意：Basic Auth 字段。
        StrInput(
            name="username",
            display_name="Username",
            info="",
            show=False,
            value="",
            required=False,
            real_time_refresh=True,
        ),
        SecretStrInput(
            name="password",
            display_name="Password",
            info="",
            show=False,
            value="",
            required=False,
            real_time_refresh=True,
        ),
        # 注意：其他常见认证字段。
        StrInput(
            name="domain",
            display_name="Domain",
            info="",
            show=False,
            value="",
            required=False,
            real_time_refresh=True,
        ),
        StrInput(
            name="base_url",
            display_name="Base URL",
            info="",
            show=False,
            value="",
            required=False,
            real_time_refresh=True,
        ),
        SecretStrInput(
            name="bearer_token",
            display_name="Bearer Token",
            info="",
            show=False,
            value="",
            required=False,
            real_time_refresh=True,
        ),
        SecretStrInput(
            name="authorization_code",
            display_name="Authorization Code",
            info="",
            show=False,
            value="",
            required=False,
            real_time_refresh=True,
        ),
        StrInput(
            name="scopes",
            display_name="Scopes",
            info="",
            show=False,
            value="",
            required=False,
            real_time_refresh=True,
        ),
        # 注意：补充常见认证字段。
        StrInput(
            name="subdomain",
            display_name="Subdomain",
            info="",
            show=False,
            value="",
            required=False,
            real_time_refresh=True,
        ),
        StrInput(
            name="instance_url",
            display_name="Instance URL",
            info="",
            show=False,
            value="",
            required=False,
            real_time_refresh=True,
        ),
        StrInput(
            name="tenant_id",
            display_name="Tenant ID",
            info="",
            show=False,
            value="",
            required=False,
            real_time_refresh=True,
        ),
        SortableListInput(
            name="action_button",
            display_name="Action",
            placeholder="Select action",
            options=[],
            value="disabled",
            helper_text="Please connect before selecting actions.",
            helper_text_metadata={"variant": "destructive"},
            show=True,
            required=False,
            real_time_refresh=True,
            limit=1,
        ),
    ]

    _name_sanitizer = re.compile(r"[^a-zA-Z0-9_-]")

    # 注意：类级缓存，跨实例复用以降低 SDK 调用频次。
    _actions_cache: dict[str, dict[str, Any]] = {}
    _action_schema_cache: dict[str, dict[str, Any]] = {}
    # 注意：全局跟踪所有工具集发现的认证字段名，避免误删。
    _all_auth_field_names: set[str] = set()

    @classmethod
    def get_actions_cache(cls) -> dict[str, dict[str, Any]]:
        """获取类级 action 缓存。
        契约：返回 `toolkit_slug -> action data` 的字典引用。
        关键路径：直接返回类级缓存引用。
        决策：返回引用而非拷贝。问题：减少内存与拷贝成本；方案：暴露引用；代价：调用方误改风险；重评：出现缓存污染时改为深拷贝。
        """
        return cls._actions_cache

    @classmethod
    def get_action_schema_cache(cls) -> dict[str, dict[str, Any]]:
        """获取类级 action schema 缓存。
        契约：返回 `toolkit_slug -> schema` 的字典引用。
        关键路径：直接返回类级缓存引用。
        决策：返回引用以节省复制。问题：schema 体积大；方案：共享引用；代价：误改风险；重评：出现跨实例污染时改为深拷贝。
        """
        return cls._action_schema_cache

    @classmethod
    def get_all_auth_field_names(cls) -> set[str]:
        """获取已发现的认证字段名集合。
        契约：返回集合引用；调用方应避免直接移除元素。
        关键路径：直接返回类级集合引用。
        决策：共享集合以去重字段。问题：多实例重复发现；方案：类级集合；代价：生命周期变长；重评：若需隔离实例时拆分为实例级。
        """
        return cls._all_auth_field_names

    outputs = [
        Output(name="dataFrame", display_name="DataFrame", method="as_dataframe"),
    ]

    inputs = list(_base_inputs)

    def __init__(self, **kwargs):
        """初始化实例状态，避免跨组件共享可变数据。
        契约：重置实例级缓存与映射；副作用为覆盖同名实例属性。
        关键路径：构建集合/映射并清空实例级缓存。
        决策：显式初始化所有字段。问题：共享可变状态易污染；方案：__init__ 统一置空；代价：初始化成本增加；重评：性能瓶颈时改为惰性初始化。
        """
        super().__init__(**kwargs)
        self._all_fields: set[str] = set()
        self._bool_variables: set[str] = set()
        self._actions_data: dict[str, dict[str, Any]] = {}
        self._default_tools: set[str] = set()
        self._display_to_key_map: dict[str, str] = {}
        self._key_to_display_map: dict[str, str] = {}
        self._sanitized_names: dict[str, str] = {}
        self._action_schemas: dict[str, Any] = {}
        # 注意：实例级 toolkit schema 缓存，避免重复请求。
        self._toolkit_schema: dict[str, Any] | None = None
        # 注意：跟踪动态认证字段，便于隐藏/显示/重置。
        self._auth_dynamic_fields: set[str] = set()

    def as_message(self) -> Message:
        """以 `Message` 形式返回动作结果。
        契约：返回 `Message(text=...)`；结果为空时返回提示文本；失败语义为底层异常透传。
        关键路径：执行 action → 转换为 Message。
        决策：空结果返回提示文本。问题：UI 需可读反馈；方案：文本兜底；代价：可能掩盖上游异常；重评：当需要严格失败语义时。
        """
        result = self.execute_action()
        if result is None:
            return Message(text="Action execution returned no result")
        return Message(text=str(result))

    def as_dataframe(self) -> DataFrame:
        """以 `DataFrame` 形式返回动作结果。
        契约：返回 `DataFrame`，dict 结果会包成单行列表；失败语义为底层异常透传。
        关键路径：执行 action → 规范化结果 → 构建 DataFrame。
        决策：重命名 `data` 列避免日志探测冲突。问题：`.data` 属性探测误判；方案：改为 `_data`；代价：列名变化；重评：日志探测机制调整时。
        """
        # 安全：Astra 云环境不支持该组件，提前失败避免远程调用。
        raise_error_if_astra_cloud_disable_component(disable_component_in_astra_cloud_msg)
        result = self.execute_action()

        if isinstance(result, dict):
            result = [result]
        # 注意：避免暴露 `data` 列名，防止日志探测 `.data` 误判为属性。
        df = DataFrame(result)
        if "data" in df.columns:
            df = df.rename(columns={"data": "_data"})
        return df

    def as_data(self) -> Data:
        """以 `Data` 形式返回动作结果。
        契约：包装结果到 `Data(results=...)`；失败语义为底层异常透传。
        关键路径：执行 action → 包装 Data。
        决策：固定输出 Data 结构。问题：下游统一消费；方案：总是包装；代价：失去原始类型；重评：当输出协议变更时。
        """
        result = self.execute_action()
        return Data(results=result)

    def _build_action_maps(self):
        """构建 action 名称的双向映射与清理名缓存。

        关键路径：
        1) 生成 display_name → key 与 key → display_name
        2) 生成清理后的 action 名用于标签/显示
        """
        if not self._display_to_key_map or not self._key_to_display_map:
            self._display_to_key_map = {data["display_name"]: key for key, data in self._actions_data.items()}
            self._key_to_display_map = {key: data["display_name"] for key, data in self._actions_data.items()}
            self._sanitized_names = {
                action: self._name_sanitizer.sub("-", self.sanitize_action_name(action))
                for action in self._actions_data
            }

    def sanitize_action_name(self, action_name: str) -> str:
        """将 action key 转为展示名。
        契约：若无匹配映射则回退为原值。
        关键路径：构建映射 → 查表返回。
        决策：无映射时回退原值。问题：避免 UI 为空；方案：直接返回输入；代价：显示可能不友好；重评：当强制校验映射时。
        """
        self._build_action_maps()
        return self._key_to_display_map.get(action_name, action_name)

    def desanitize_action_name(self, action_name: str) -> str:
        """将展示名转回 action key。
        契约：若无匹配映射则回退为原值。
        关键路径：构建映射 → 查表返回。
        决策：无映射时回退原值。问题：避免 action 丢失；方案：直接返回输入；代价：可能执行失败；重评：当需要严格校验时。
        """
        self._build_action_maps()
        return self._display_to_key_map.get(action_name, action_name)

    def _get_action_fields(self, action_key: str | None) -> set[str]:
        """获取指定 action 的字段集合。

        契约：action_key 为空或不存在时返回空集合。
        """
        if action_key is None:
            return set()
        return set(self._actions_data[action_key]["action_fields"]) if action_key in self._actions_data else set()

    def _build_wrapper(self) -> Composio:
        """构建 Composio SDK 包装实例。

        契约：必须提供 `api_key`。
        失败语义：`api_key` 缺失或 SDK 构建失败时抛 `ValueError`。
        排障入口：日志关键字 `Composio wrapper`。
        """
        # 安全：Astra 云环境不支持该组件，提前失败避免远程调用。
        raise_error_if_astra_cloud_disable_component(disable_component_in_astra_cloud_msg)
        try:
            if not self.api_key:
                msg = "Composio API Key is required"
                raise ValueError(msg)
            return Composio(api_key=self.api_key, provider=LangchainProvider())

        except ValueError as e:
            logger.error(f"Error building Composio wrapper: {e}")
            msg = "Please provide a valid Composio API Key in the component settings"
            raise ValueError(msg) from e

    def show_hide_fields(self, build_config: dict, field_value: Any):
        """按 action 选择更新字段显隐，避免全量重建。
        契约：仅调整 `show/value`，不改变字段结构；副作用为原地修改 build_config。
        关键路径：解析 action → 计算应显示字段 → 更新显隐与值。
        决策：仅改显隐不重建字段。问题：频繁重建影响 UI；方案：最小变更；代价：旧字段可能残留配置；重评：出现 UI 不一致时。
        """
        if not field_value:
            for field in self._all_fields:
                build_config[field]["show"] = False
                if field in self._bool_variables:
                    build_config[field]["value"] = False
                else:
                    build_config[field]["value"] = ""
            return

        action_key = None
        if isinstance(field_value, list) and field_value:
            action_key = self.desanitize_action_name(field_value[0]["name"])
        else:
            action_key = field_value

        fields_to_show = self._get_action_fields(action_key)

        for field in self._all_fields:
            should_show = field in fields_to_show
            if build_config[field]["show"] != should_show:
                build_config[field]["show"] = should_show
                if not should_show:
                    if field in self._bool_variables:
                        build_config[field]["value"] = False
                    else:
                        build_config[field]["value"] = ""

    def _populate_actions_data(self):
        """拉取工具动作并构建缓存与字段映射。

        关键路径：
        1) 命中类级缓存时深拷贝到实例
        2) 调用 Composio SDK 拉取 raw tools 与 schema
        3) 解析字段/版本并写入缓存与映射

        异常流：工具缺失或 schema 解析失败时跳过该动作并记录日志。
        性能瓶颈：SDK 网络调用与 schema flatten。
        排障入口：日志关键字 `actions` / `flatten_schema` / `toolkit`。
        """
        if self._actions_data:
            return

        # 性能：优先命中类级缓存，避免重复 SDK 调用。
        toolkit_slug = self.app_name.lower()
        if toolkit_slug in self.__class__.get_actions_cache():
            # 注意：深拷贝避免实例修改污染全局缓存。
            self._actions_data = copy.deepcopy(self.__class__.get_actions_cache()[toolkit_slug])
            self._action_schemas = copy.deepcopy(self.__class__.get_action_schema_cache().get(toolkit_slug, {}))
            logger.debug(f"Loaded actions for {toolkit_slug} from in-process cache")
            return

        api_key = getattr(self, "api_key", None)
        if not api_key:
            logger.warning("API key is missing. Cannot populate actions data.")
            return

        try:
            composio = self._build_wrapper()
            toolkit_slug = self.app_name.lower()

            raw_tools = composio.tools.get_raw_composio_tools(toolkits=[toolkit_slug], limit=999)

            if not raw_tools:
                msg = f"Toolkit '{toolkit_slug}' not found or has no available tools"
                raise ValueError(msg)

            for raw_tool in raw_tools:
                try:
                    # 实现：统一为 dict 结构，便于 .get 读取。
                    tool_dict = raw_tool.__dict__ if hasattr(raw_tool, "__dict__") else raw_tool

                    if not tool_dict:
                        logger.warning(f"Tool is None or empty: {raw_tool}")
                        continue

                    action_key = tool_dict.get("slug")
                    if not action_key:
                        logger.warning(f"Action key (slug) is missing in tool: {tool_dict}")
                        continue

                    # 注意：优先使用人类可读名称，保证 UI 可读性。
                    display_name = tool_dict.get("name") or tool_dict.get("display_name")
                    if not display_name:
                        # 注意：兜底转换，如 GMAIL_SEND_EMAIL → "Send Email"。
                        # 实现：移除应用前缀并做标题化。
                        clean_name = action_key
                        clean_name = clean_name.removeprefix(f"{self.app_name.upper()}_")
                        # 实现：下划线转空格并 title 化。
                        display_name = clean_name.replace("_", " ").title()

                    # 实现：构建参数名列表，并收集布尔字段。
                    parameters_schema = tool_dict.get("input_parameters", {})
                    if parameters_schema is None:
                        logger.warning(f"Parameters schema is None for action key: {action_key}")
                        # 注意：schema 缺失时仍保留 action，避免 UI 断裂。
                        # 实现：记录版本信息以便执行时使用。
                        version = tool_dict.get("version")
                        available_versions = tool_dict.get("available_versions", [])

                        self._action_schemas[action_key] = tool_dict
                        self._actions_data[action_key] = {
                            "display_name": display_name,
                            "action_fields": [],
                            "file_upload_fields": set(),
                            "version": version,
                            "available_versions": available_versions,
                        }
                        continue

                    try:
                        # 注意：处理非标准 schema 结构，避免解析失败。
                        if not isinstance(parameters_schema, dict):
                            # 实现：尝试从模型对象提取 dict。
                            if hasattr(parameters_schema, "model_dump"):
                                parameters_schema = parameters_schema.model_dump()
                            elif hasattr(parameters_schema, "__dict__"):
                                parameters_schema = parameters_schema.__dict__
                            else:
                                logger.warning(f"Cannot process parameters schema for {action_key}, skipping")
                                # 注意：保留 action 但字段为空，避免 UI 断裂。
                                version = tool_dict.get("version")
                                available_versions = tool_dict.get("available_versions", [])

                                self._action_schemas[action_key] = tool_dict
                                self._actions_data[action_key] = {
                                    "display_name": display_name,
                                    "action_fields": [],
                                    "file_upload_fields": set(),
                                    "version": version,
                                    "available_versions": available_versions,
                                }
                                continue

                        # 注意：schema 缺少核心字段时，用最小结构兜底。
                        if not parameters_schema.get("properties") and not parameters_schema.get("$defs"):
                            # 实现：补齐最小合法结构。
                            parameters_schema = {"type": "object", "properties": {}}

                        # 注意：required 为 None 会导致 flatten 失败，先规范化。
                        if parameters_schema.get("required") is None:
                            parameters_schema = parameters_schema.copy()  # 注意：避免修改原始对象。
                            parameters_schema["required"] = []

                        try:
                            # 实现：保留原始 description，flatten 后回填。
                            original_descriptions = {}
                            original_props = parameters_schema.get("properties", {})
                            for prop_name, prop_schema in original_props.items():
                                if isinstance(prop_schema, dict) and "description" in prop_schema:
                                    original_descriptions[prop_name] = prop_schema["description"]

                            flat_schema = flatten_schema(parameters_schema)

                            # 注意：flatten 后可能丢失描述，需要回补。
                            if flat_schema and isinstance(flat_schema, dict) and "properties" in flat_schema:
                                flat_props = flat_schema["properties"]
                                for field_name, field_schema in flat_props.items():
                                    # 实现：如果描述缺失，尝试从原始字段恢复。
                                    if isinstance(field_schema, dict) and "description" not in field_schema:
                                        # 注意：数组字段 bcc[0] → bcc。
                                        base_field_name = field_name.replace("[0]", "")
                                        if base_field_name in original_descriptions:
                                            field_schema["description"] = original_descriptions[base_field_name]
                                        elif field_name in original_descriptions:
                                            field_schema["description"] = original_descriptions[field_name]
                        except (KeyError, TypeError, ValueError):
                            # 注意：flatten 失败时保留 action，避免 UI 断裂。
                            version = tool_dict.get("version")
                            available_versions = tool_dict.get("available_versions", [])

                            self._action_schemas[action_key] = tool_dict
                            self._actions_data[action_key] = {
                                "display_name": display_name,
                                "action_fields": [],
                                "file_upload_fields": set(),
                                "version": version,
                                "available_versions": available_versions,
                            }
                            continue

                        if flat_schema is None:
                            logger.warning(f"Flat schema is None for action key: {action_key}")
                            # 注意：schema 为空仍保留 action，避免 UI 断裂。
                            version = tool_dict.get("version")
                            available_versions = tool_dict.get("available_versions", [])

                            self._action_schemas[action_key] = tool_dict
                            self._actions_data[action_key] = {
                                "display_name": display_name,
                                "action_fields": [],
                                "file_upload_fields": set(),
                                "version": version,
                                "available_versions": available_versions,
                            }
                            continue

                        # 实现：提取字段并识别文件上传字段。
                        raw_action_fields = list(flat_schema.get("properties", {}).keys())
                        action_fields = []
                        attachment_related_found = False
                        file_upload_fields = set()

                        # 注意：需要从原始 schema 中识别 file_uploadable。
                        original_props = parameters_schema.get("properties", {})

                        # 注意：顶层 object/array 字段用单一 JSON 输入呈现。
                        json_parent_fields = set()
                        for top_name, top_schema in original_props.items():
                            if isinstance(top_schema, dict) and top_schema.get("type") in {"object", "array"}:
                                json_parent_fields.add(top_name)

                        for field_name, field_schema in original_props.items():
                            if isinstance(field_schema, dict):
                                clean_field_name = field_name.replace("[0]", "")
                                # 实现：直接标记 file_uploadable。
                                if field_schema.get("file_uploadable") is True:
                                    file_upload_fields.add(clean_field_name)

                                # 注意：anyOf 结构也可能标记 file_uploadable。
                                if "anyOf" in field_schema:
                                    for any_of_item in field_schema["anyOf"]:
                                        if isinstance(any_of_item, dict) and any_of_item.get("file_uploadable") is True:
                                            file_upload_fields.add(clean_field_name)

                        for field in raw_action_fields:
                            clean_field = field.replace("[0]", "")
                            # 注意：JSON 父字段下的子项不单独暴露。
                            top_prefix = clean_field.split(".")[0].split("[")[0]
                            if top_prefix in json_parent_fields and "." in clean_field:
                                continue
                            # 注意：附件子字段统一合并为 attachment。
                            if clean_field.lower().startswith("attachment."):
                                attachment_related_found = True
                                continue  # Skip individual attachment fields

                            # 注意：保留字冲突时加前缀避免覆盖组件属性。
                            if clean_field in self.RESERVED_ATTRIBUTES:
                                clean_field = f"{self.app_name}_{clean_field}"

                            action_fields.append(clean_field)

                        # 注意：发现附件子字段时统一添加 attachment 字段。
                        if attachment_related_found:
                            action_fields.append("attachment")
                            file_upload_fields.add("attachment")  # 注意：attachment 也是文件上传字段。

                        # 注意：确保 JSON 父字段本身存在。
                        for parent in json_parent_fields:
                            if parent not in action_fields:
                                action_fields.append(parent)

                        # 注意：记录布尔字段，便于执行前类型校正。
                        properties = flat_schema.get("properties", {})
                        if properties:
                            for p_name, p_schema in properties.items():
                                if isinstance(p_schema, dict) and p_schema.get("type") == "boolean":
                                    # 实现：使用清洗后的字段名做布尔追踪。
                                    clean_field_name = p_name.replace("[0]", "")
                                    self._bool_variables.add(clean_field_name)

                        # 实现：记录版本信息供执行阶段使用。
                        version = tool_dict.get("version")
                        available_versions = tool_dict.get("available_versions", [])

                        self._action_schemas[action_key] = tool_dict
                        self._actions_data[action_key] = {
                            "display_name": display_name,
                            "action_fields": action_fields,
                            "file_upload_fields": file_upload_fields,
                            "version": version,
                            "available_versions": available_versions,
                        }

                    except (KeyError, TypeError, ValueError) as flatten_error:
                        logger.error(f"flatten_schema failed for {action_key}: {flatten_error}")
                        # 注意：解析失败仍保留 action，避免 UI 断裂。
                        version = tool_dict.get("version")
                        available_versions = tool_dict.get("available_versions", [])

                        self._action_schemas[action_key] = tool_dict
                        self._actions_data[action_key] = {
                            "display_name": display_name,
                            "action_fields": [],
                            "file_upload_fields": set(),
                            "version": version,
                            "available_versions": available_versions,
                        }
                        continue

                except ValueError as e:
                    logger.warning(f"Failed processing Composio tool for action {raw_tool}: {e}")

            # 实现：聚合所有字段，供 UI 隐藏/展示使用。
            self._all_fields = {f for d in self._actions_data.values() for f in d["action_fields"]}
            self._build_action_maps()

            # 性能：写入类级缓存，后续实例避免重复调用 SDK。
            self.__class__.get_actions_cache()[toolkit_slug] = copy.deepcopy(self._actions_data)
            self.__class__.get_action_schema_cache()[toolkit_slug] = copy.deepcopy(self._action_schemas)

        except ValueError as e:
            logger.debug(f"Could not populate Composio actions for {self.app_name}: {e}")

    def _validate_schema_inputs(self, action_key: str) -> list[InputTypes]:
        """将指定 action 的 JSON schema 转换为 Langflow 输入对象。

        关键路径：
        1) 读取并规范化 schema（含 required/description）
        2) flatten + 字段清洗（含附件合并与保留字处理）
        3) 生成输入组件并标记必填/高级/文件类型

        异常流：schema 缺失或解析失败时返回空列表并记录日志。
        性能瓶颈：flatten_schema 与字段遍历。
        排障入口：日志关键字 `Flat schema` / `schema` / `action key`。
        """
        # 注意：占位/默认值不做校验与渲染。
        if action_key in ("disabled", "placeholder", ""):
            logger.debug(f"Skipping schema validation for placeholder value: {action_key}")
            return []

        schema_dict = self._action_schemas.get(action_key)
        if not schema_dict:
            logger.warning(f"No schema found for action key: {action_key}")
            return []

        try:
            parameters_schema = schema_dict.get("input_parameters", {})
            if parameters_schema is None:
                logger.warning(f"Parameters schema is None for action key: {action_key}")
                return []

            # 注意：schema 结构异常直接返回空，避免 UI 崩溃。
            if not isinstance(parameters_schema, dict):
                logger.warning(
                    f"Parameters schema is not a dict for action key: {action_key}, got: {type(parameters_schema)}"
                )
                return []

            # 注意：缺少 properties/$defs 时提供最小结构兜底。
            if not parameters_schema.get("properties") and not parameters_schema.get("$defs"):
                # 实现：补齐最小合法 schema。
                parameters_schema = {"type": "object", "properties": {}}

            # 注意：required 为 None 会导致 flatten 失败，先规范化。
            if parameters_schema.get("required") is None:
                parameters_schema = parameters_schema.copy()  # 注意：避免修改原始对象。
                parameters_schema["required"] = []

            # 注意：保留原始 required 以支持 JSON 父字段处理。
            original_required = set(parameters_schema.get("required", []))

            try:
                # 实现：保留原始 description，flatten 后回填。
                original_descriptions = {}
                original_props = parameters_schema.get("properties", {})
                for prop_name, prop_schema in original_props.items():
                    if isinstance(prop_schema, dict) and "description" in prop_schema:
                        original_descriptions[prop_name] = prop_schema["description"]

                flat_schema = flatten_schema(parameters_schema)

                # 注意：flatten 后可能丢失描述，需要回补。
                if flat_schema and isinstance(flat_schema, dict) and "properties" in flat_schema:
                    flat_props = flat_schema["properties"]
                    for field_name, field_schema in flat_props.items():
                        # 实现：描述缺失时从原始字段恢复。
                        if isinstance(field_schema, dict) and "description" not in field_schema:
                            # 注意：数组字段 bcc[0] → bcc。
                            base_field_name = field_name.replace("[0]", "")
                            if base_field_name in original_descriptions:
                                field_schema["description"] = original_descriptions[base_field_name]
                            elif field_name in original_descriptions:
                                field_schema["description"] = original_descriptions[field_name]
            except (KeyError, TypeError, ValueError) as flatten_error:
                logger.error(f"flatten_schema failed for {action_key}: {flatten_error}")
                return []

            if flat_schema is None:
                logger.warning(f"Flat schema is None for action key: {action_key}")
                return []

            # 注意：保障 flat_schema 为 dict 结构。
            if not isinstance(flat_schema, dict):
                logger.warning(f"Flat schema is not a dict for action key: {action_key}, got: {type(flat_schema)}")
                return []

            # 注意：确保 schema 类型为 object，满足输入模型构建要求。
            if flat_schema.get("type") != "object":
                logger.warning(f"Flat schema for {action_key} is not of type 'object', got: {flat_schema.get('type')}")
                # 实现：补齐类型，避免后续报错。
                flat_schema["type"] = "object"

            if "properties" not in flat_schema:
                flat_schema["properties"] = {}

            # 实现：清洗字段名，移除数组字段 [0] 后缀。
            cleaned_properties = {}
            attachment_related_fields = set()  # Track fields that are attachment-related

            for field_name, field_schema in flat_schema.get("properties", {}).items():
                # 实现：去除数组字段后缀（如 "bcc[0]" -> "bcc"）。
                clean_field_name = field_name.replace("[0]", "")

                # 注意：附件子字段统一合并为 attachment。
                if clean_field_name.lower().startswith("attachment."):
                    attachment_related_fields.add(clean_field_name)
                    # 注意：不暴露附件子字段，避免 UI 混乱。
                    continue

                # 注意：保留字冲突时加前缀，避免覆盖组件属性。
                if clean_field_name in self.RESERVED_ATTRIBUTES:
                    original_name = clean_field_name
                    clean_field_name = f"{self.app_name}_{clean_field_name}"
                    # 注意：同步更新描述，提示字段已重命名。
                    field_schema_copy = field_schema.copy()
                    original_description = field_schema.get("description", "")
                    field_schema_copy["description"] = (
                        f"{original_name.replace('_', ' ').title()} for {self.app_name.title()}: {original_description}"
                    ).strip()
                else:
                    # 实现：非冲突字段沿用原 schema。
                    field_schema_copy = field_schema

                # 注意：保留完整 schema 信息（不止 type）。
                cleaned_properties[clean_field_name] = field_schema_copy

            # 注意：发现附件子字段时补一个统一的 attachment 字段。
            if attachment_related_fields:
                # 实现：使用通用附件 schema。
                attachment_schema = {
                    "type": "string",
                    "description": "File attachment for the email",
                    "title": "Attachment",
                }
                cleaned_properties["attachment"] = attachment_schema

            # 实现：用清洗后的字段名更新 schema。
            flat_schema["properties"] = cleaned_properties

            # 注意：required 字段需同步使用清洗后的名称。
            if flat_schema.get("required"):
                cleaned_required = []
                for field in flat_schema["required"]:
                    base = field.replace("[0]", "")
                    if base in self.RESERVED_ATTRIBUTES:
                        cleaned_required.append(f"{self.app_name}_{base}")
                    else:
                        cleaned_required.append(base)
                flat_schema["required"] = cleaned_required

            input_schema = create_input_schema_from_json_schema(flat_schema)
            if input_schema is None:
                logger.warning(f"Input schema is None for action key: {action_key}")
                return []

            # 注意：输入模型缺失字段时直接返回，避免空指针。
            if not hasattr(input_schema, "model_fields"):
                logger.warning(f"Input schema for {action_key} does not have model_fields attribute")
                return []

            if input_schema.model_fields is None:
                logger.warning(f"Input schema model_fields is None for {action_key}")
                return []

            result = schema_to_langflow_inputs(input_schema)

            # 实现：处理附件字段与 advanced 标记。
            if result:
                processed_inputs = []
                required_fields_set = set(flat_schema.get("required", []))

                # 实现：获取文件上传字段集合。
                file_upload_fields = self._actions_data.get(action_key, {}).get("file_upload_fields", set())
                if attachment_related_fields:  # If we consolidated attachment fields
                    file_upload_fields = file_upload_fields | {"attachment"}

                # 注意：顶层 JSON 父字段需作为单一输入展示。
                top_props_for_json = set()
                props_dict = parameters_schema.get("properties", {}) if isinstance(parameters_schema, dict) else {}
                for top_name, top_schema in props_dict.items():
                    if isinstance(top_schema, dict) and top_schema.get("type") in {"object", "array"}:
                        top_props_for_json.add(top_name)

                for inp in result:
                    if hasattr(inp, "name") and inp.name is not None:
                        # 注意：跳过 JSON 父字段的子项（含数组前缀）。
                        raw_prefix = inp.name.split(".")[0]
                        base_prefix = raw_prefix.replace("[0]", "")
                        if base_prefix in top_props_for_json and ("." in inp.name or "[" in inp.name):
                            continue
                        # 注意：文件上传字段改用 FileInput。
                        if inp.name.lower() in file_upload_fields or inp.name.lower() == "attachment":
                            # 实现：文件字段转为 FileInput。
                            file_input = FileInput(
                                name=inp.name,
                                display_name=getattr(inp, "display_name", inp.name.replace("_", " ").title()),
                                required=inp.name in required_fields_set,
                                advanced=inp.name not in required_fields_set,
                                info=getattr(inp, "info", "Upload file for this field"),
                                show=True,
                                file_types=[
                                    "csv",
                                    "txt",
                                    "doc",
                                    "docx",
                                    "xls",
                                    "xlsx",
                                    "pdf",
                                    "png",
                                    "jpg",
                                    "jpeg",
                                    "gif",
                                    "zip",
                                    "rar",
                                    "ppt",
                                    "pptx",
                                ],
                            )
                            processed_inputs.append(file_input)
                        else:
                            # 实现：补齐 display_name/info，避免空提示。
                            if not hasattr(inp, "display_name") or not inp.display_name:
                                inp.display_name = inp.name.replace("_", " ").title()

                            # 注意：优先使用 schema 描述。
                            field_schema = flat_schema.get("properties", {}).get(inp.name, {})
                            schema_description = field_schema.get("description")
                            current_info = getattr(inp, "info", None)

                            # 实现：描述优先级：schema > 现有 info > 字段名兜底。
                            if schema_description:
                                inp.info = schema_description
                            elif not current_info:
                                # 注意：无描述时用字段名生成兜底说明。
                                inp.info = f"{inp.name.replace('_', ' ').title()} field"

                            # 实现：非必填字段标记为高级。
                            if inp.name not in required_fields_set:
                                inp.advanced = True

                            # 注意：避免 entity_id 被错误映射到 user_id。
                            if inp.name in {"user_id", f"{self.app_name}_user_id"} and getattr(
                                self, "entity_id", None
                            ) == getattr(inp, "value", None):
                                continue

                            processed_inputs.append(inp)
                    else:
                        processed_inputs.append(inp)

                # 实现：为每个 JSON 父字段增加单一多行输入。
                props_dict = parameters_schema.get("properties", {}) if isinstance(parameters_schema, dict) else {}
                for top_name in top_props_for_json:
                    # 注意：避免重复插入。
                    if any(getattr(i, "name", None) == top_name for i in processed_inputs):
                        continue
                    top_schema = props_dict.get(top_name, {})
                    # 注意：复杂对象/数组使用 MultilineInput。
                    is_required = top_name in original_required
                    processed_inputs.append(
                        MultilineInput(
                            name=top_name,
                            display_name=top_schema.get("title") or top_name.replace("_", " ").title(),
                            info=(
                                top_schema.get("description") or "Provide JSON for this parameter (object or array)."
                            ),
                            required=is_required,  # Setting original schema
                        )
                    )

                return processed_inputs
            return result  # noqa: TRY300
        except ValueError as e:
            logger.warning(f"Error generating inputs for {action_key}: {e}")
            return []

    def _get_inputs_for_all_actions(self) -> dict[str, list[InputTypes]]:
        """返回 action_key → 输入组件列表 的映射。

        契约：针对已加载的 action 生成输入列表。
        失败语义：schema 解析失败的 action 返回空列表。
        """
        result: dict[str, list[InputTypes]] = {}
        for key in self._actions_data:
            result[key] = self._validate_schema_inputs(key)
        return result

    def _remove_inputs_from_build_config(self, build_config: dict, keep_for_action: str) -> None:
        """移除其他 action 的参数字段，保留当前 action 的字段。

        契约：仅移除非保护字段；原地修改 build_config。
        """
        protected_keys = {"code", "entity_id", "api_key", "auth_link", "action_button", "tool_mode"}

        for action_key, lf_inputs in self._get_inputs_for_all_actions().items():
            if action_key == keep_for_action:
                continue
            for inp in lf_inputs:
                if inp.name is not None and inp.name not in protected_keys:
                    build_config.pop(inp.name, None)

    def _update_action_config(self, build_config: dict, selected_value: Any) -> None:
        """为选定 action 添加/更新参数输入字段。

        关键路径：
        1) 解析选中的 action key
        2) 生成输入字段并移除其它 action 字段
        3) 写回 build_config 并更新字段集合
        """
        if not selected_value:
            return

        # 注意：UI 可能传入列表字典或 raw key。
        if isinstance(selected_value, list) and selected_value:
            display_name = selected_value[0]["name"]
        else:
            display_name = selected_value

        action_key = self.desanitize_action_name(display_name)

        # 注意：占位/默认值不触发字段生成。
        if action_key in ("disabled", "placeholder", ""):
            logger.debug(f"Skipping action config update for placeholder value: {action_key}")
            return

        lf_inputs = self._validate_schema_inputs(action_key)

        # 实现：先清理其他 action 的字段。
        self._remove_inputs_from_build_config(build_config, action_key)

        # 实现：新增或更新当前 action 字段。
        for inp in lf_inputs:
            if inp.name is not None:
                inp_dict = inp.to_dict() if hasattr(inp, "to_dict") else inp.__dict__.copy()

                # 注意：避免修改 input_types 配置，保持原值。

                inp_dict.setdefault("show", True)  # visible once action selected
                # 注意：保留用户已输入的值。
                if inp.name in build_config:
                    existing_val = build_config[inp.name].get("value")
                    inp_dict.setdefault("value", existing_val)
                build_config[inp.name] = inp_dict

        # 实现：同步字段集合，便于后续显隐控制。
        self._all_fields.update({i.name for i in lf_inputs if i.name is not None})

        # 注意：统一 input_types，避免 None 触发前端报错。
        self.update_input_types(build_config)

    def _is_tool_mode_enabled(self) -> bool:
        """判断当前是否启用 tool_mode。"""
        return getattr(self, "tool_mode", False)

    def _set_action_visibility(self, build_config: dict, *, force_show: bool | None = None) -> None:
        """根据 tool_mode 或强制值设置 action 字段显隐。"""
        if force_show is not None:
            build_config["action_button"]["show"] = force_show
        else:
            # 注意：tool_mode 开启时隐藏 action 选择。
            build_config["action_button"]["show"] = not self._is_tool_mode_enabled()

    def create_new_auth_config(self, app_name: str) -> str:
        """为指定应用创建新的认证配置。
        契约：返回 auth_config_id；失败语义为 SDK 异常抛 `ValueError`。
        关键路径：构建 wrapper → 调用 auth_configs.create。
        决策：默认使用 Composio 托管配置。问题：减少用户输入；方案：use_composio_managed_auth；代价：可定制性下降；重评：需要自定义认证时。
        """
        composio = self._build_wrapper()
        auth_config = composio.auth_configs.create(toolkit=app_name, options={"type": "use_composio_managed_auth"})
        return auth_config.id

    def _initiate_connection(self, app_name: str) -> tuple[str, str]:
        """通过 link 方法发起连接，返回 (redirect_url, connection_id)。

        关键路径：
        1) 创建新的 auth_config
        2) 调用 link 获取重定向 URL 与连接 ID
        3) 校验返回并记录日志

        异常流：URL/ID 缺失或格式异常时抛 `ValueError`。
        排障入口：日志关键字 `Connection initiated`。
        """
        try:
            composio = self._build_wrapper()

            # 注意：保持旧行为，始终创建新 auth config。
            auth_config_id = self.create_new_auth_config(app_name)

            connection_request = composio.connected_accounts.link(user_id=self.entity_id, auth_config_id=auth_config_id)

            redirect_url = getattr(connection_request, "redirect_url", None)
            connection_id = getattr(connection_request, "id", None)

            if not redirect_url or not redirect_url.startswith(("http://", "https://")):
                msg = "Invalid redirect URL received from Composio"
                raise ValueError(msg)

            if not connection_id:
                msg = "No connection ID received from Composio"
                raise ValueError(msg)

            logger.info(f"Connection initiated for {app_name}: {redirect_url} (ID: {connection_id})")
            return redirect_url, connection_id  # noqa: TRY300

        except (ValueError, ConnectionError, TypeError, AttributeError) as e:
            logger.error(f"Error initiating connection for {app_name}: {e}")
            msg = f"Failed to initiate connection: {e}"
            raise ValueError(msg) from e

    def _check_connection_status_by_id(self, connection_id: str) -> str | None:
        """查询指定连接状态。

        契约：返回状态字符串或 None（未找到/异常）。
        排障入口：日志关键字 `Connection`。
        """
        try:
            composio = self._build_wrapper()
            connection = composio.connected_accounts.get(nanoid=connection_id)
            status = getattr(connection, "status", None)
            logger.info(f"Connection {connection_id} status: {status}")
        except (ValueError, ConnectionError) as e:
            logger.error(f"Error checking connection {connection_id}: {e}")
            return None
        else:
            return status

    def _find_active_connection_for_app(self, app_name: str) -> tuple[str, str] | None:
        """查找当前用户在指定应用下的 ACTIVE 连接。

        契约：返回 (connection_id, status) 或 None。
        失败语义：SDK 异常时返回 None 并记录日志。
        """
        try:
            composio = self._build_wrapper()
            connection_list = composio.connected_accounts.list(
                user_ids=[self.entity_id], toolkit_slugs=[app_name.lower()]
            )

            if connection_list and hasattr(connection_list, "items") and connection_list.items:
                for connection in connection_list.items:
                    connection_id = getattr(connection, "id", None)
                    connection_status = getattr(connection, "status", None)
                    if connection_status == "ACTIVE" and connection_id:
                        logger.info(f"Found existing ACTIVE connection for {app_name}: {connection_id}")
                        return connection_id, connection_status

        except (ValueError, ConnectionError) as e:
            logger.error(f"Error finding active connection for {app_name}: {e}")
            return None
        else:
            return None

    def _get_connection_auth_info(self, connection_id: str) -> tuple[str | None, bool | None]:
        """获取连接的认证信息 (auth_scheme, is_composio_managed)。

        契约：获取失败时返回 (None, None)。
        """
        try:
            composio = self._build_wrapper()
            connection = composio.connected_accounts.get(nanoid=connection_id)
            auth_config = getattr(connection, "auth_config", None)
            if auth_config is None and hasattr(connection, "__dict__"):
                auth_config = getattr(connection.__dict__, "auth_config", None)
            scheme = getattr(auth_config, "auth_scheme", None) if auth_config else None
            is_managed = getattr(auth_config, "is_composio_managed", None) if auth_config else None
        except (AttributeError, ValueError, ConnectionError, TypeError) as e:
            logger.debug(f"Could not retrieve auth info for connection {connection_id}: {e}")
            return None, None
        else:
            return scheme, is_managed

    def _disconnect_specific_connection(self, connection_id: str) -> None:
        """断开指定连接。

        副作用：删除远端连接记录。
        失败语义：SDK 异常时抛 `ValueError`。
        """
        try:
            composio = self._build_wrapper()
            composio.connected_accounts.delete(nanoid=connection_id)
            logger.info(f"✅ Disconnected specific connection: {connection_id}")

        except Exception as e:
            logger.error(f"Error disconnecting connection {connection_id}: {e}")
            msg = f"Failed to disconnect connection {connection_id}: {e}"
            raise ValueError(msg) from e

    def _to_plain_dict(self, obj: Any) -> Any:
        """递归将 SDK 模型转换为可 `.get` 的原生 dict/list。

        契约：无法转换时返回原对象。
        """
        try:
            if isinstance(obj, dict):
                return {k: self._to_plain_dict(v) for k, v in obj.items()}
            if isinstance(obj, (list, tuple, set)):
                return [self._to_plain_dict(v) for v in obj]
            if hasattr(obj, "model_dump"):
                try:
                    return self._to_plain_dict(obj.model_dump())
                except (TypeError, AttributeError, ValueError):
                    pass
            if hasattr(obj, "__dict__") and not isinstance(obj, (str, bytes)):
                try:
                    return self._to_plain_dict({k: v for k, v in obj.__dict__.items() if not k.startswith("_")})
                except (TypeError, AttributeError, ValueError):
                    pass
        except (TypeError, ValueError, AttributeError, RecursionError):
            return obj
        else:
            return obj

    def _get_toolkit_schema(self) -> dict[str, Any] | None:
        """拉取并缓存工具集 schema（用于认证模式/字段）。"""
        if self._toolkit_schema is not None:
            return self._toolkit_schema
        try:
            composio = self._build_wrapper()
            app_slug = getattr(self, "app_name", "").lower()
            if not app_slug:
                return None
            # 注意：使用 SDK 的 toolkits.get 接口拉取 schema。
            schema = composio.toolkits.get(slug=app_slug)
            self._toolkit_schema = self._to_plain_dict(schema)
        except (AttributeError, ValueError, ConnectionError, TypeError) as e:
            logger.debug(f"Could not retrieve toolkit schema for {getattr(self, 'app_name', '')}: {e}")
            return None
        else:
            return self._toolkit_schema

    def _extract_auth_modes_from_schema(self, schema: dict[str, Any] | None) -> list[str]:
        """从 schema 提取可用认证模式（如 OAUTH2、API_KEY）。"""
        if not schema:
            return []
        modes: list[str] = []
        # 注意：composio_managed_auth_schemes 为托管方案列表。
        managed = schema.get("composio_managed_auth_schemes") or schema.get("composioManagedAuthSchemes") or []
        has_managed_schemes = isinstance(managed, list) and len(managed) > 0

        # 注意：存在托管方案时，将 Composio_Managed 置顶。
        if has_managed_schemes:
            modes.append("Composio_Managed")

        # 注意：auth_config_details 中包含 mode 信息。
        details = schema.get("auth_config_details") or schema.get("authConfigDetails") or []
        for item in details:
            mode = item.get("mode") or item.get("auth_method")
            if isinstance(mode, str) and mode not in modes:
                modes.append(mode)
        return modes

    def _render_auth_mode_dropdown(self, build_config: dict, modes: list[str]) -> None:
        """渲染 auth_mode 控件；单一模式时以 TabInput 形式展示。"""
        try:
            build_config.setdefault("auth_mode", {})
            auth_mode_cfg = build_config["auth_mode"]
            # 注意：优先使用已连接的 scheme，避免用户误切换。
            stored_scheme = (build_config.get("auth_link") or {}).get("auth_scheme")
            if isinstance(stored_scheme, str) and stored_scheme:
                modes = [stored_scheme]

            if len(modes) <= 1:
                # 注意：单一模式用 TabInput pill 展示。
                selected = modes[0] if modes else ""
                try:
                    pill = TabInput(
                        name="auth_mode",
                        display_name="Auth Mode",
                        options=[selected] if selected else [],
                        value=selected,
                    ).to_dict()
                    pill["show"] = True
                    build_config["auth_mode"] = pill
                except (TypeError, ValueError, AttributeError):
                    build_config["auth_mode"] = {
                        "name": "auth_mode",
                        "display_name": "Auth Mode",
                        "type": "tab",
                        "options": [selected],
                        "value": selected,
                        "show": True,
                    }
            else:
                # 注意：多模式下使用下拉框，隐藏 pill。
                auth_mode_cfg["options"] = modes
                auth_mode_cfg["show"] = True
                if not auth_mode_cfg.get("value") and modes:
                    auth_mode_cfg["value"] = modes[0]
                if "auth_mode_display" in build_config:
                    build_config["auth_mode_display"]["show"] = False
            auth_mode_cfg["helper_text"] = "Choose how to authenticate with the toolkit."
        except (TypeError, ValueError, AttributeError) as e:
            logger.debug(f"Failed to render auth_mode dropdown: {e}")

    def _insert_field_before_action_button(self, build_config: dict, field_name: str, field_data: dict) -> None:
        """将字段插入 action_button 之前，保持 UI 顺序稳定。
        契约：保持 build_config 引用不变，仅调整键顺序。
        """
        # 注意：字段已存在时不重复插入。
        if field_name in build_config:
            return

        # 注意：没有 action_button 时直接追加。
        if "action_button" not in build_config:
            build_config[field_name] = field_data
            return

        keys_before_action = []
        keys_after_action = []
        found_action = False

        for key in list(build_config.keys()):
            if key == "action_button":
                found_action = True
                keys_after_action.append(key)
            elif found_action:
                keys_after_action.append(key)
            else:
                keys_before_action.append(key)

        new_config = {}

        for key in keys_before_action:
            new_config[key] = build_config[key]

        new_config[field_name] = field_data

        for key in keys_after_action:
            new_config[key] = build_config[key]

        # 注意：保留 build_config 引用，避免上层失效。
        build_config.clear()
        build_config.update(new_config)

    def _clear_auth_dynamic_fields(self, build_config: dict) -> None:
        for fname in list(self._auth_dynamic_fields):
            if fname in build_config and isinstance(build_config[fname], dict):
                # 注意：仅隐藏并重置，避免 UI 不刷新。
                build_config[fname]["show"] = False
                build_config[fname]["value"] = ""
                build_config[fname]["required"] = False
        self._auth_dynamic_fields.clear()

    def _add_text_field(
        self,
        build_config: dict,
        name: str,
        display_name: str,
        info: str | None,
        *,
        required: bool,
        default_value: str | None = None,
    ) -> None:
        """在 build_config 中新增或更新认证输入字段。"""
        # 注意：若字段已存在（占位字段），仅更新显示属性。
        if name in build_config:
            build_config[name]["display_name"] = display_name or name.replace("_", " ").title()
            build_config[name]["info"] = info or ""
            build_config[name]["required"] = required
            build_config[name]["show"] = True
            if default_value is not None and default_value != "":
                build_config[name]["value"] = default_value
        else:
            # 安全：敏感字段使用 SecretStrInput。
            sensitive_fields = {
                "client_id",
                "client_secret",
                "api_key",
                "api_key_field",
                "generic_api_key",
                "token",
                "access_token",
                "refresh_token",
                "password",
                "bearer_token",
                "authorization_code",
            }

            if name in sensitive_fields:
                field = SecretStrInput(
                    name=name,
                    display_name=display_name or name.replace("_", " ").title(),
                    info=info or "",
                    required=required,
                    real_time_refresh=True,
                    show=True,
                ).to_dict()
            else:
                field = StrInput(
                    name=name,
                    display_name=display_name or name.replace("_", " ").title(),
                    info=info or "",
                    required=required,
                    real_time_refresh=True,
                    show=True,
                ).to_dict()

            if default_value is not None and default_value != "":
                field["value"] = default_value

            self._insert_field_before_action_button(build_config, name, field)

        self._auth_dynamic_fields.add(name)
        # 注意：同步写入类级集合，便于全局追踪。
        self.__class__.get_all_auth_field_names().add(name)

    def _render_custom_auth_fields(self, build_config: dict, schema: dict[str, Any], mode: str) -> None:
        """根据 schema 渲染自定义认证字段。"""
        details = schema.get("auth_config_details") or schema.get("authConfigDetails") or []
        selected = None
        for item in details:
            if (item.get("mode") or item.get("auth_method")) == mode:
                selected = item
                break
        if not selected:
            return
        fields = selected.get("fields") or {}

        # 实现：字段处理辅助函数。
        def process_fields(field_list: list, *, required: bool) -> None:
            for field in field_list:
                name = field.get("name")
                if not name:
                    continue
                # 注意：跳过 Access Token（bearer_token）。
                if name == "bearer_token":
                    continue
                # 注意：有默认值的字段不在 UI 中展示。
                default_val = field.get("default")
                if default_val is not None:
                    continue
                disp = field.get("display_name") or field.get("displayName") or name
                desc = field.get("description")
                self._add_text_field(build_config, name, disp, desc, required=required, default_value=default_val)

        # 注意：仅处理 auth_config_creation 字段；连接发起字段由 Composio 页面处理。
        creation = fields.get("auth_config_creation") or fields.get("authConfigCreation") or {}
        process_fields(creation.get("required", []), required=True)
        process_fields(creation.get("optional", []), required=False)

    def _collect_all_auth_field_names(self, schema: dict[str, Any] | None) -> set[str]:
        names: set[str] = set()
        if not schema:
            return names
        details = schema.get("auth_config_details") or schema.get("authConfigDetails") or []
        for item in details:
            fields = (item.get("fields") or {}) if isinstance(item, dict) else {}
            for section_key in (
                "auth_config_creation",
                "authConfigCreation",
                "connected_account_initiation",
                "connectedAccountInitiation",
            ):
                section = fields.get(section_key) or {}
                for bucket in ("required", "optional"):
                    for entry in section.get(bucket, []) or []:
                        name = entry.get("name") if isinstance(entry, dict) else None
                        if name:
                            names.add(name)
                            # 注意：同步到类级集合，避免跨实例遗漏。
                            self.__class__.get_all_auth_field_names().add(name)
        # 注意：仅使用 schema 中发现的字段名，不添加别名。
        return names

    def _clear_auth_fields_from_schema(self, build_config: dict, schema: dict[str, Any] | None) -> None:
        all_names = self._collect_all_auth_field_names(schema)
        for name in list(all_names):
            if name in build_config and isinstance(build_config[name], dict):
                # 注意：隐藏并重置，保证 UI 即时刷新。
                build_config[name]["show"] = False
                build_config[name]["value"] = ""
        # 注意：同步清理动态字段跟踪集合。
        self._clear_auth_dynamic_fields(build_config)

    def update_input_types(self, build_config: dict) -> dict:
        """将 build_config 中的 input_types=None 规范为 []。
        契约：返回原字典引用；副作用为就地修正字段。
        关键路径：遍历 build_config → 修正 input_types。
        决策：空值替换为 []。问题：前端不接受 None；方案：统一替换；代价：掩盖上游配置问题；重评：当上游保证类型一致时。
        """
        try:
            for key, value in list(build_config.items()):
                if isinstance(value, dict):
                    if value.get("input_types") is None:
                        build_config[key]["input_types"] = []
                elif hasattr(value, "input_types") and value.input_types is None:
                    with suppress(AttributeError, TypeError):
                        value.input_types = []
        except (RuntimeError, KeyError):
            pass
        return build_config

    def update_build_config(self, build_config: dict, field_value: Any, field_name: str | None = None) -> dict:
        """更新 build_config 的认证与 action 选择状态机。

        关键路径：
        1) 解析 tool_mode 与 api_key 状态，必要时加载 actions/schema
        2) 根据 auth_mode/连接状态渲染或清理认证字段与 action 选项
        3) 处理各类字段事件（action_button/auth_link 等）并返回规范化配置

        异常流：SDK 调用失败时回写 UI 提示并保留可重试状态。
        性能瓶颈：首次加载 actions/schema 与多次连接状态查询。
        排障入口：日志关键字 `auth_link` / `Actions populated` / `connection`。
        决策：以 build_config 为单一真相驱动 UI。问题：多来源状态易漂移；方案：集中写回 build_config；代价：逻辑复杂；重评：当引入统一状态管理器时。
        """
        # 注意：此处不再规范化 legacy input_types，交由上游修复。

        # 注意：tool_mode 可能来自多处，需全量检查。
        instance_tool_mode = getattr(self, "tool_mode", False) if hasattr(self, "tool_mode") else False

        # 实现：兼容 build_config 不同结构的 tool_mode。
        build_config_tool_mode = False
        if "tool_mode" in build_config:
            tool_mode_config = build_config["tool_mode"]
            if isinstance(tool_mode_config, dict):
                build_config_tool_mode = tool_mode_config.get("value", False)
            else:
                build_config_tool_mode = bool(tool_mode_config)

        # 注意：tool_mode 变更时必须同步实例与 build_config。
        if field_name == "tool_mode":
            self.tool_mode = field_value
            instance_tool_mode = field_value
            # 注意：持久化 tool_mode 到 build_config，避免状态丢失。
            if "tool_mode" not in build_config:
                build_config["tool_mode"] = {}
            if isinstance(build_config["tool_mode"], dict):
                build_config["tool_mode"]["value"] = field_value
            build_config_tool_mode = field_value

        # 注意：任一来源开启即视为启用。
        current_tool_mode = instance_tool_mode or build_config_tool_mode or (field_name == "tool_mode" and field_value)

        # 注意：只要有 API Key，就应确保 action 元数据可用，且必须先于早退逻辑。
        api_key_available = hasattr(self, "api_key") and self.api_key

        # 注意：判断是否需要拉取 actions，同时考虑缓存是否可用。
        actions_available = bool(self._actions_data)
        toolkit_slug = getattr(self, "app_name", "").lower()
        cached_actions_available = toolkit_slug in self.__class__.get_actions_cache()

        should_populate = False

        if (field_name == "api_key" and field_value) or (
            api_key_available and not actions_available and not cached_actions_available
        ):
            should_populate = True
        elif api_key_available and not actions_available and cached_actions_available:
            self._populate_actions_data()

        if should_populate:
            logger.info(f"Populating actions data for {getattr(self, 'app_name', 'unknown')}...")
            self._populate_actions_data()
            logger.info(f"Actions populated: {len(self._actions_data)} actions found")
            # 实现：同时拉取 schema 驱动认证 UI。
            schema = self._get_toolkit_schema()
            modes = self._extract_auth_modes_from_schema(schema)
            self._render_auth_mode_dropdown(build_config, modes)
            # 注意：已选模式且非托管时渲染自定义字段。
            try:
                selected_mode = (build_config.get("auth_mode") or {}).get("value")
                managed = (schema or {}).get("composio_managed_auth_schemes") or []
                # 注意：Composio_Managed 或 token 模式无需自定义字段。
                token_modes = ["API_KEY", "BEARER_TOKEN", "BASIC"]
                if selected_mode and selected_mode not in ["Composio_Managed", *token_modes]:
                    self._clear_auth_dynamic_fields(build_config)
                    self._render_custom_auth_fields(build_config, schema or {}, selected_mode)
                elif selected_mode in token_modes:
                    # 注意：token 模式需要清理已有字段。
                    self._clear_auth_dynamic_fields(build_config)
            except (TypeError, ValueError, AttributeError):
                pass

        # 注意：只要有 actions，就必须刷新 action 选项。
        if self._actions_data:
            self._build_action_maps()
            build_config["action_button"]["options"] = [
                {"name": self.sanitize_action_name(action), "metadata": action} for action in self._actions_data
            ]
            logger.info(f"Action options set in build_config: {len(build_config['action_button']['options'])} options")
            # 注意：actions 可用时同步刷新 auth_mode。
            schema = self._get_toolkit_schema()
            modes = self._extract_auth_modes_from_schema(schema)
            self._render_auth_mode_dropdown(build_config, modes)
        else:
            build_config["action_button"]["options"] = []
            logger.warning("No actions found, setting empty options")

        # 注意：API key 变更时清理旧连接状态。
        if field_name == "api_key" and field_value:
            stored_connection_before = build_config.get("auth_link", {}).get("connection_id")
            if "auth_link" in build_config and "connection_id" in build_config["auth_link"]:
                build_config["auth_link"].pop("connection_id", None)
                build_config["auth_link"]["value"] = "connect"
                build_config["auth_link"]["auth_tooltip"] = "Connect"
                logger.info(f"Cleared stored connection_id '{stored_connection_before}' due to API key change")
            else:
                logger.info("DEBUG: EARLY No stored connection_id to clear on API key change")
            # 注意：清理已存 auth scheme 并重置 auth_mode。
            build_config.setdefault("auth_link", {})
            build_config["auth_link"].pop("auth_scheme", None)
            build_config.setdefault("auth_mode", {})
            build_config["auth_mode"].pop("value", None)
            build_config["auth_mode"]["show"] = True
            # 注意：若 auth_mode 为 pill，切回下拉框配置。
            if isinstance(build_config.get("auth_mode"), dict) and build_config["auth_mode"].get("type") == "tab":
                build_config["auth_mode"].pop("type", None)
            # 实现：用新 API key 上下文重建下拉选项。
            try:
                schema = self._get_toolkit_schema()
                modes = self._extract_auth_modes_from_schema(schema)
                # 实现：强制重建 DropdownInput，保证渲染一致。
                dd = DropdownInput(
                    name="auth_mode",
                    display_name="Auth Mode",
                    options=modes,
                    placeholder="Select auth mode",
                    toggle=True,
                    toggle_disable=True,
                    show=True,
                    real_time_refresh=True,
                    helper_text="Choose how to authenticate with the toolkit.",
                ).to_dict()
                build_config["auth_mode"] = dd
            except (TypeError, ValueError, AttributeError):
                pass
            # 注意：重新输入 API key 时清空 action 选择并隐藏字段。
            try:
                if "action_button" in build_config and isinstance(build_config["action_button"], dict):
                    build_config["action_button"]["value"] = "disabled"
                self._hide_all_action_fields(build_config)
            except (TypeError, ValueError, AttributeError):
                pass

        # 注意：处理 tool_mode 下的断开操作。
        if field_name == "auth_link" and field_value == "disconnect":
            # 注意：软断开仅清本地状态，不删除远端连接。
            stored_connection_id = build_config.get("auth_link", {}).get("connection_id")
            if not stored_connection_id:
                logger.warning("No connection ID found to disconnect (soft)")
            build_config.setdefault("auth_link", {})
            build_config["auth_link"]["value"] = "connect"
            build_config["auth_link"]["auth_tooltip"] = "Connect"
            build_config["auth_link"].pop("connection_id", None)
            build_config["action_button"]["helper_text"] = "Please connect before selecting actions."
            build_config["action_button"]["helper_text_metadata"] = {"variant": "destructive"}
            return self.update_input_types(build_config)

        # 注意：切换 auth_mode 时根据 schema 渲染字段。
        if field_name == "auth_mode":
            schema = self._get_toolkit_schema() or {}
            # 注意：切换前先清理旧字段。
            self._clear_auth_fields_from_schema(build_config, schema)
            mode = field_value if isinstance(field_value, str) else (build_config.get("auth_mode", {}).get("value"))
            if not mode and isinstance(build_config.get("auth_mode"), dict):
                mode = build_config["auth_mode"].get("value")
            # 注意：任何模式都需要 auth_link 控件。
            build_config.setdefault("auth_link", {})
            build_config["auth_link"]["show"] = False
            # 注意：切换模式时重置连接状态。
            build_config["auth_link"].pop("connection_id", None)
            build_config["auth_link"].pop("auth_config_id", None)
            build_config["auth_link"]["value"] = "connect"
            build_config["auth_link"]["auth_tooltip"] = "Connect"
            # 注意：已有 ACTIVE 连接时不渲染任何认证字段。
            existing_active = self._find_active_connection_for_app(self.app_name)
            if existing_active:
                connection_id, _ = existing_active
                self._clear_auth_fields_from_schema(build_config, schema)
                build_config.setdefault("create_auth_config", {})
                build_config["create_auth_config"]["show"] = False
                build_config["auth_link"]["value"] = "validated"
                build_config["auth_link"]["auth_tooltip"] = "Disconnect"
                build_config["auth_link"]["connection_id"] = connection_id
                # 注意：在 UI 中回显已连接的 auth scheme。
                scheme, _ = self._get_connection_auth_info(connection_id)
                if scheme:
                    build_config.setdefault("auth_link", {})
                    build_config["auth_link"]["auth_scheme"] = scheme
                    build_config.setdefault("auth_mode", {})
                    build_config["auth_mode"]["value"] = scheme
                    build_config["auth_mode"]["options"] = [scheme]
                    build_config["auth_mode"]["show"] = False
                    try:
                        pill = TabInput(
                            name="auth_mode",
                            display_name="Auth Mode",
                            options=[scheme],
                            value=scheme,
                        ).to_dict()
                        pill["show"] = True
                        build_config["auth_mode"] = pill
                    except (TypeError, ValueError, AttributeError):
                        build_config["auth_mode"] = {
                            "name": "auth_mode",
                            "display_name": "Auth Mode",
                            "type": "tab",
                            "options": [scheme],
                            "value": scheme,
                            "show": True,
                        }
                    build_config["action_button"]["helper_text"] = ""
                    build_config["action_button"]["helper_text_metadata"] = {}
                    return self.update_input_types(build_config)
            if mode:
                managed = schema.get("composio_managed_auth_schemes") or []
                # 注意：Create Auth Config 仅内部使用，始终隐藏。
                build_config.setdefault("create_auth_config", {})
                build_config["create_auth_config"]["show"] = False
                build_config["create_auth_config"]["display_name"] = ""
                build_config["create_auth_config"]["value"] = ""
                build_config["create_auth_config"]["helper_text"] = ""
                build_config["create_auth_config"]["options"] = ["create"]
                if mode == "Composio_Managed":
                    # 注意：Composio_Managed 无需额外字段。
                    pass
                elif mode in ["API_KEY", "BEARER_TOKEN", "BASIC"]:
                    # 注意：token 模式字段在 Composio 页面填写。
                    pass
                elif isinstance(managed, list) and mode in managed:
                    # 注意：托管模式也允许自定义，需要渲染字段。
                    self._render_custom_auth_fields(build_config, schema, mode)
                else:
                    # 注意：自定义模式按 schema 渲染字段。
                    self._render_custom_auth_fields(build_config, schema, mode)
                return self.update_input_types(build_config)

        # 注意：处理 tool_mode 下的连接发起流程。
        if field_name == "auth_link" and isinstance(field_value, dict):
            try:
                toolkit_slug = self.app_name.lower()

                # 注意：优先复用 ACTIVE 连接。
                existing_active = self._find_active_connection_for_app(self.app_name)
                if existing_active:
                    connection_id, _ = existing_active
                    build_config["auth_link"]["value"] = "validated"
                    build_config["auth_link"]["auth_tooltip"] = "Disconnect"
                    build_config["auth_link"]["connection_id"] = connection_id
                    build_config["action_button"]["helper_text"] = ""
                    build_config["action_button"]["helper_text_metadata"] = {}

                    # 注意：连接成功后清理认证字段。
                    schema = self._get_toolkit_schema()
                    self._clear_auth_fields_from_schema(build_config, schema)

                    # 注意：连接后将 auth_mode 切换为 pill 展示。
                    scheme, _ = self._get_connection_auth_info(connection_id)
                    if scheme:
                        build_config.setdefault("auth_mode", {})
                        build_config["auth_mode"]["value"] = scheme
                        build_config["auth_mode"]["options"] = [scheme]
                        build_config["auth_mode"]["show"] = False
                        try:
                            pill = TabInput(
                                name="auth_mode",
                                display_name="Auth Mode",
                                options=[scheme],
                                value=scheme,
                            ).to_dict()
                            pill["show"] = True
                            build_config["auth_mode"] = pill
                        except (TypeError, ValueError, AttributeError):
                            build_config["auth_mode"] = {
                                "name": "auth_mode",
                                "display_name": "Auth Mode",
                                "type": "tab",
                                "options": [scheme],
                                "value": scheme,
                                "show": True,
                            }

                    logger.info(f"Using existing ACTIVE connection {connection_id} for {toolkit_slug}")
                    return self.update_input_types(build_config)

                # 注意：仅复用 ACTIVE 连接，其余状态需新建。
                stored_connection_id = None

                # 注意：仅在无可用连接时才创建新连接。
                if existing_active is None:
                    # 注意：已存在重定向 URL 时不重复创建。
                    current_auth_link_value = build_config.get("auth_link", {}).get("value", "")
                    if current_auth_link_value and current_auth_link_value.startswith(("http://", "https://")):
                        # 注意：已有 URL 直接复用。
                        logger.info(f"Redirect URL already exists for {toolkit_slug}, skipping new creation")
                        return self.update_input_types(build_config)

                    try:
                        # 实现：读取当前 auth_mode。
                        schema = self._get_toolkit_schema()
                        mode = None
                        if isinstance(build_config.get("auth_mode"), dict):
                            mode = build_config["auth_mode"].get("value")
                        # 注意：缺少默认托管配置时必须先选择模式。
                        managed = (schema or {}).get("composio_managed_auth_schemes") or []

                        # 注意：显式处理 Composio_Managed。
                        if mode == "Composio_Managed":
                            # 实现：走 Composio_Managed 流程。
                            redirect_url, connection_id = self._initiate_connection(toolkit_slug)
                            build_config["auth_link"]["value"] = redirect_url
                            logger.info(f"New OAuth URL created for {toolkit_slug}: {redirect_url}")
                            return self.update_input_types(build_config)

                        if not mode:
                            build_config["auth_link"]["value"] = "connect"
                            build_config["auth_link"]["auth_tooltip"] = "Select Auth Mode"
                            return self.update_input_types(build_config)
                        # 注意：自定义模式需要创建 auth_config；仅 OAUTH2 校验必填字段。
                        required_missing = []
                        if mode == "OAUTH2":
                            req_names_pre = self._get_schema_field_names(
                                schema,
                                "OAUTH2",
                                "auth_config_creation",
                                "required",
                            )
                            for fname in req_names_pre:
                                if fname in build_config:
                                    val = build_config[fname].get("value")
                                    if val in (None, ""):
                                        required_missing.append(fname)
                        if required_missing:
                            # 注意：逐字段提示缺失信息。
                            for fname in required_missing:
                                if fname in build_config and isinstance(build_config[fname], dict):
                                    build_config[fname]["helper_text"] = "This field is required"
                                    build_config[fname]["helper_text_metadata"] = {"variant": "destructive"}
                                    # 注意：同步写入 info，确保可见。
                                    existing_info = build_config[fname].get("info") or ""
                                    build_config[fname]["info"] = f"Required: {existing_info}".strip()
                                    build_config[fname]["show"] = True
                            # 注意：在 auth_mode 提示缺失字段。
                            build_config.setdefault("auth_mode", {})
                            missing_joined = ", ".join(required_missing)
                            build_config["auth_mode"]["helper_text"] = f"Missing required: {missing_joined}"
                            build_config["auth_mode"]["helper_text_metadata"] = {"variant": "destructive"}
                            build_config["auth_link"]["value"] = "connect"
                            build_config["auth_link"]["auth_tooltip"] = f"Missing: {missing_joined}"
                            return self.update_input_types(build_config)
                        composio = self._build_wrapper()
                        if mode == "OAUTH2":
                            # 注意：若已创建 auth_config，直接复用。
                            stored_ac_id = (build_config.get("auth_link") or {}).get("auth_config_id")
                            if stored_ac_id:
                                # 注意：已有重定向 URL 则不重复创建。
                                current_link_value = build_config.get("auth_link", {}).get("value", "")
                                if current_link_value and current_link_value.startswith(("http://", "https://")):
                                    logger.info(
                                        f"Redirect URL already exists for {toolkit_slug} OAUTH2, skipping new creation"
                                    )
                                    return self.update_input_types(build_config)

                                # 实现：link 方法发起连接，无需收集连接发起字段。
                                redirect = composio.connected_accounts.link(
                                    user_id=self.entity_id,
                                    auth_config_id=stored_ac_id,
                                )
                                redirect_url = getattr(redirect, "redirect_url", None)
                                connection_id = getattr(redirect, "id", None)
                                if redirect_url:
                                    build_config["auth_link"]["value"] = redirect_url
                                if connection_id:
                                    build_config["auth_link"]["connection_id"] = connection_id
                                # 注意：成功后清理 action 阻断提示。
                                build_config["action_button"]["helper_text"] = ""
                                build_config["action_button"]["helper_text_metadata"] = {}
                                # 注意：连接后清理认证字段。
                                schema = self._get_toolkit_schema()
                                self._clear_auth_fields_from_schema(build_config, schema)
                                return self.update_input_types(build_config)
                            # 注意：否则按 schema 必填字段创建 OAuth2 auth_config。
                            credentials = {}
                            missing = []
                            # 实现：从 schema 收集必填字段。
                            req_names = self._get_schema_field_names(
                                schema,
                                "OAUTH2",
                                "auth_config_creation",
                                "required",
                            )
                            candidate_names = set(self._auth_dynamic_fields) | req_names
                            for fname in candidate_names:
                                if fname in build_config:
                                    val = build_config[fname].get("value")
                                    if val not in (None, ""):
                                        credentials[fname] = val
                                    else:
                                        missing.append(fname)
                            # 注意：可选项缺失仍继续，后端校验为准。
                            # 注意：已有重定向 URL 则不重复创建。
                            current_link_value = build_config.get("auth_link", {}).get("value", "")
                            if current_link_value and current_link_value.startswith(("http://", "https://")):
                                logger.info(
                                    f"Redirect URL already exists for {toolkit_slug} OAUTH2, skipping new creation"
                                )
                                return self.update_input_types(build_config)

                            ac = composio.auth_configs.create(
                                toolkit=toolkit_slug,
                                options={
                                    "type": "use_custom_auth",
                                    "auth_scheme": "OAUTH2",
                                    "credentials": credentials,
                                },
                            )
                            auth_config_id = getattr(ac, "id", None)
                            # 实现：直接 link，无需处理连接发起字段。
                            redirect = composio.connected_accounts.link(
                                user_id=self.entity_id,
                                auth_config_id=auth_config_id,
                            )
                            redirect_url = getattr(redirect, "redirect_url", None)
                            connection_id = getattr(redirect, "id", None)
                            if redirect_url:
                                build_config["auth_link"]["value"] = redirect_url
                            if connection_id:
                                build_config["auth_link"]["connection_id"] = connection_id
                            # 注意：连接成功后立即隐藏认证字段。
                            schema = self._get_toolkit_schema()
                            self._clear_auth_fields_from_schema(build_config, schema)
                            build_config["action_button"]["helper_text"] = ""
                            build_config["action_button"]["helper_text_metadata"] = {}
                            return self.update_input_types(build_config)
                        if mode == "API_KEY":
                            # 注意：已有重定向 URL 则不重复创建。
                            current_link_value = build_config.get("auth_link", {}).get("value", "")
                            if current_link_value and current_link_value.startswith(("http://", "https://")):
                                logger.info(
                                    f"Redirect URL already exists for {toolkit_slug} API_KEY, skipping new creation"
                                )
                                return self.update_input_types(build_config)

                            ac = composio.auth_configs.create(
                                toolkit=toolkit_slug,
                                options={"type": "use_custom_auth", "auth_scheme": "API_KEY", "credentials": {}},
                            )
                            auth_config_id = getattr(ac, "id", None)
                            # 注意：link 方式由用户在 Composio 页面填写 API Key。
                            initiation = composio.connected_accounts.link(
                                user_id=self.entity_id,
                                auth_config_id=auth_config_id,
                            )
                            connection_id = getattr(initiation, "id", None)
                            redirect_url = getattr(initiation, "redirect_url", None)
                            # 注意：API_KEY 也会返回重定向 URL。
                            if redirect_url:
                                build_config["auth_link"]["value"] = redirect_url
                                build_config["auth_link"]["auth_tooltip"] = "Disconnect"
                            # 注意：连接成功后立即隐藏认证字段。
                            schema = self._get_toolkit_schema()
                            self._clear_auth_fields_from_schema(build_config, schema)
                            build_config["action_button"]["helper_text"] = ""
                            build_config["action_button"]["helper_text_metadata"] = {}

                            return self.update_input_types(build_config)
                        # 注意：其他模式走通用自定义流程（类似 API_KEY）。
                        # 注意：已有重定向 URL 则不重复创建。
                        current_link_value = build_config.get("auth_link", {}).get("value", "")
                        if current_link_value and current_link_value.startswith(("http://", "https://")):
                            logger.info(f"Redirect URL already exists for {toolkit_slug} {mode}, skipping new creation")
                            return self.update_input_types(build_config)

                        ac = composio.auth_configs.create(
                            toolkit=toolkit_slug,
                            options={"type": "use_custom_auth", "auth_scheme": mode, "credentials": {}},
                        )
                        auth_config_id = getattr(ac, "id", None)
                        # 注意：link 方式由用户在 Composio 页面填写必要信息。
                        initiation = composio.connected_accounts.link(
                            user_id=self.entity_id,
                            auth_config_id=auth_config_id,
                        )
                        connection_id = getattr(initiation, "id", None)
                        redirect_url = getattr(initiation, "redirect_url", None)
                        if redirect_url:
                            build_config["auth_link"]["value"] = redirect_url
                            build_config["auth_link"]["auth_tooltip"] = "Disconnect"
                        # 注意：连接成功后清理认证字段。
                        schema = self._get_toolkit_schema()
                        self._clear_auth_fields_from_schema(build_config, schema)
                        build_config["action_button"]["helper_text"] = ""
                        build_config["action_button"]["helper_text_metadata"] = {}
                        return self.update_input_types(build_config)
                    except (ValueError, ConnectionError, TypeError) as e:
                        logger.error(f"Error creating connection: {e}")
                        build_config["auth_link"]["value"] = "connect"
                        build_config["auth_link"]["auth_tooltip"] = f"Error: {e!s}"
                    else:
                        return self.update_input_types(build_config)
                else:
                    # 注意：已有可用连接，不再发起 OAuth。
                    build_config["auth_link"]["auth_tooltip"] = "Disconnect"

            except (ValueError, ConnectionError) as e:
                logger.error(f"Error in connection initiation: {e}")
                build_config["auth_link"]["value"] = "connect"
                build_config["auth_link"]["auth_tooltip"] = f"Error: {e!s}"
                build_config["action_button"]["helper_text"] = "Please connect before selecting actions."
                build_config["action_button"]["helper_text_metadata"] = {"variant": "destructive"}
                return build_config

        # 注意：检查 ACTIVE 连接并更新状态（tool_mode 下也适用）。
        if hasattr(self, "api_key") and self.api_key:
            stored_connection_id = build_config.get("auth_link", {}).get("connection_id")
            active_connection_id = None

            # 注意：优先验证已存 connection_id。
            if stored_connection_id:
                status = self._check_connection_status_by_id(stored_connection_id)
                if status == "ACTIVE":
                    active_connection_id = stored_connection_id

            # 注意：若已存连接不可用，则全量查找 ACTIVE。
            if not active_connection_id:
                active_connection = self._find_active_connection_for_app(self.app_name)
                if active_connection:
                    active_connection_id, _ = active_connection
                    # 注意：缓存 ACTIVE 连接 ID 供后续复用。
                    if "auth_link" not in build_config:
                        build_config["auth_link"] = {}
                    build_config["auth_link"]["connection_id"] = active_connection_id

            if active_connection_id:
                # 注意：标记连接已验证。
                build_config["auth_link"]["value"] = "validated"
                build_config["auth_link"]["auth_tooltip"] = "Disconnect"
                build_config["auth_link"]["show"] = False
                # 注意：回显已连接的 auth scheme。
                scheme, _ = self._get_connection_auth_info(active_connection_id)
                if scheme:
                    build_config.setdefault("auth_link", {})
                    build_config["auth_link"]["auth_scheme"] = scheme
                    build_config.setdefault("auth_mode", {})
                    build_config["auth_mode"]["value"] = scheme
                    build_config["auth_mode"]["options"] = [scheme]
                    build_config["auth_mode"]["show"] = False
                    try:
                        pill = TabInput(
                            name="auth_mode",
                            display_name="Auth Mode",
                            options=[scheme],
                            value=scheme,
                        ).to_dict()
                        pill["show"] = True
                        build_config["auth_mode"] = pill
                    except (TypeError, ValueError, AttributeError):
                        build_config["auth_mode"] = {
                            "name": "auth_mode",
                            "display_name": "Auth Mode",
                            "type": "tab",
                            "options": [scheme],
                            "value": scheme,
                            "show": True,
                        }
                    build_config["action_button"]["helper_text"] = ""
                    build_config["action_button"]["helper_text_metadata"] = {}
                # 注意：已连接时清理认证字段。
                schema = self._get_toolkit_schema()
                self._clear_auth_fields_from_schema(build_config, schema)
                build_config.setdefault("create_auth_config", {})
                build_config["create_auth_config"]["show"] = False
                build_config["action_button"]["helper_text"] = ""
                build_config["action_button"]["helper_text_metadata"] = {}
            else:
                build_config["auth_link"]["value"] = "connect"
                build_config["auth_link"]["auth_tooltip"] = "Connect"
                build_config["action_button"]["helper_text"] = "Please connect before selecting actions."
                build_config["action_button"]["helper_text_metadata"] = {"variant": "destructive"}

        # 注意：任何来源启用 tool_mode 时，隐藏 action UI 但保留认证流程。
        if current_tool_mode:
            build_config["action_button"]["show"] = False

            # 注意：tool_mode 下隐藏所有 action 参数字段。
            for field in self._all_fields:
                if field in build_config:
                    build_config[field]["show"] = False

            # 注意：同时隐藏 build_config 中其他潜在 action 字段。
            for field_name_in_config in build_config:  # noqa: PLC0206
                # 注意：保留基础字段与动态认证字段。
                if (
                    field_name_in_config
                    not in [
                        "api_key",
                        "tool_mode",
                        "action_button",
                        "auth_link",
                        "entity_id",
                        "auth_mode",
                        "auth_mode_pill",
                    ]
                    and field_name_in_config not in getattr(self, "_auth_dynamic_fields", set())
                    and isinstance(build_config[field_name_in_config], dict)
                    and "show" in build_config[field_name_in_config]
                ):
                    build_config[field_name_in_config]["show"] = False

            # 注意：确保 tool_mode 状态写回 build_config。
            if "tool_mode" not in build_config:
                build_config["tool_mode"] = {"value": True}
            elif isinstance(build_config["tool_mode"], dict):
                build_config["tool_mode"]["value"] = True
            # 注意：保留认证 UI，必要时渲染字段。
            build_config.setdefault("auth_link", {})
            build_config["auth_link"]["show"] = False
            build_config["auth_link"]["display_name"] = ""

            # 注意：仅在未连接时渲染认证字段。
            active_connection = self._find_active_connection_for_app(self.app_name)
            if not active_connection:
                try:
                    schema = self._get_toolkit_schema()
                    mode = (build_config.get("auth_mode") or {}).get("value")
                    managed = (schema or {}).get("composio_managed_auth_schemes") or []
                    token_modes = ["API_KEY", "BEARER_TOKEN", "BASIC"]
                    if (
                        mode
                        and mode not in ["Composio_Managed", *token_modes]
                        and not getattr(self, "_auth_dynamic_fields", set())
                    ):
                        self._render_custom_auth_fields(build_config, schema or {}, mode)
                except (TypeError, ValueError, AttributeError):
                    pass
            else:
                # 注意：已连接时清理可能残留的认证字段。
                self._clear_auth_dynamic_fields(build_config)
            # 注意：此处不早退，继续允许认证流程执行。

        if field_name == "tool_mode":
            if field_value is True:
                build_config["action_button"]["show"] = False  # 注意：tool_mode 开启时隐藏 action。
                for field in self._all_fields:
                    build_config[field]["show"] = False  # 注意：同步隐藏所有 action 字段。
            elif field_value is False:
                build_config["action_button"]["show"] = True  # 注意：tool_mode 关闭时显示 action。
                for field in self._all_fields:
                    build_config[field]["show"] = True  # 注意：同步显示所有 action 字段。
            return self.update_input_types(build_config)

        if field_name == "action_button":
            # 注意：取消/清空选择时移除已生成字段。
            def _is_cleared(val: Any) -> bool:
                return (
                    not val
                    or (
                        isinstance(val, list)
                        and (len(val) == 0 or (len(val) > 0 and isinstance(val[0], dict) and not val[0].get("name")))
                    )
                    or (isinstance(val, str) and val in ("", "disabled", "placeholder"))
                )

            if _is_cleared(field_value):
                self._hide_all_action_fields(build_config)
                return self.update_input_types(build_config)

            self._update_action_config(build_config, field_value)
            # 注意：沿用现有显示/隐藏策略。
            self.show_hide_fields(build_config, field_value)
            return self.update_input_types(build_config)

        # 注意：处理“创建认证配置”按钮。
        if field_name == "create_auth_config" and field_value == "create":
            try:
                # 注意：已有重定向 URL 时不重复创建。
                current_link_value = build_config.get("auth_link", {}).get("value", "")
                if current_link_value and current_link_value.startswith(("http://", "https://")):
                    logger.info("Redirect URL already exists, skipping new auth config creation")
                    return self.update_input_types(build_config)

                composio = self._build_wrapper()
                toolkit_slug = self.app_name.lower()
                schema = self._get_toolkit_schema() or {}
                # 实现：收集当前 build_config 中必填字段。
                credentials = {}
                req_names = self._get_schema_field_names(schema, "OAUTH2", "auth_config_creation", "required")
                candidate_names = set(self._auth_dynamic_fields) | req_names
                for fname in candidate_names:
                    if fname in build_config:
                        val = build_config[fname].get("value")
                        if val not in (None, ""):
                            credentials[fname] = val
                # 实现：用采集到的 credentials 创建 auth config。
                ac = composio.auth_configs.create(
                    toolkit=toolkit_slug,
                    options={"type": "use_custom_auth", "auth_scheme": "OAUTH2", "credentials": credentials},
                )
                auth_config_id = getattr(ac, "id", None)
                build_config.setdefault("auth_link", {})
                if auth_config_id:
                    # 实现：直接 link，无需连接发起字段。
                    connection_request = composio.connected_accounts.link(
                        user_id=self.entity_id, auth_config_id=auth_config_id
                    )
                    redirect_url = getattr(connection_request, "redirect_url", None)
                    connection_id = getattr(connection_request, "id", None)
                    if redirect_url and redirect_url.startswith(("http://", "https://")):
                        build_config["auth_link"]["value"] = redirect_url
                        build_config["auth_link"]["auth_tooltip"] = "Disconnect"
                        build_config["auth_link"]["connection_id"] = connection_id
                        build_config["action_button"]["helper_text"] = ""
                        build_config["action_button"]["helper_text_metadata"] = {}
                        logger.info(f"New OAuth URL created for {toolkit_slug}: {redirect_url}")
                    else:
                        logger.error(f"Failed to initiate connection with new auth config: {redirect_url}")
                        build_config["auth_link"]["value"] = "error"
                        build_config["auth_link"]["auth_tooltip"] = f"Error: {redirect_url}"
                else:
                    logger.error(f"Failed to create new auth config for {toolkit_slug}")
                    build_config["auth_link"]["value"] = "error"
                    build_config["auth_link"]["auth_tooltip"] = "Create Auth Config failed"
            except (ValueError, ConnectionError, TypeError) as e:
                logger.error(f"Error creating new auth config: {e}")
                build_config["auth_link"]["value"] = "error"
                build_config["auth_link"]["auth_tooltip"] = f"Error: {e!s}"
            return self.update_input_types(build_config)

        # 注意：处理 API key 被清空。
        if field_name == "api_key" and len(field_value) == 0:
            build_config["auth_link"]["value"] = ""
            build_config["auth_link"]["auth_tooltip"] = "Please provide a valid Composio API Key."
            build_config["action_button"]["options"] = []
            build_config["action_button"]["helper_text"] = "Please connect before selecting actions."
            build_config["action_button"]["helper_text_metadata"] = {"variant": "destructive"}
            build_config.setdefault("auth_link", {})
            build_config["auth_link"].pop("connection_id", None)
            build_config["auth_link"].pop("auth_scheme", None)
            # 注意：恢复 auth_mode 下拉框并隐藏 pill。
            try:
                dd = DropdownInput(
                    name="auth_mode",
                    display_name="Auth Mode",
                    options=[],
                    placeholder="Select auth mode",
                    toggle=True,
                    toggle_disable=True,
                    show=True,
                    real_time_refresh=True,
                    helper_text="Choose how to authenticate with the toolkit.",
                ).to_dict()
                build_config["auth_mode"] = dd
            except (TypeError, ValueError, AttributeError):
                build_config.setdefault("auth_mode", {})
                build_config["auth_mode"]["show"] = True
                build_config["auth_mode"].pop("value", None)
            # 注意：API key 清空时清理 action 选择与字段。
            try:
                if "action_button" in build_config and isinstance(build_config["action_button"], dict):
                    build_config["action_button"]["value"] = "disabled"
                self._hide_all_action_fields(build_config)
            except (TypeError, ValueError, AttributeError):
                pass
            return self.update_input_types(build_config)

        # 注意：没有 API key 时不进入连接逻辑。
        if not hasattr(self, "api_key") or not self.api_key:
            return self.update_input_types(build_config)

        # 注意：tool_mode 启用时跳过连接逻辑，避免意外弹链。
        if current_tool_mode:
            build_config["action_button"]["show"] = False
            return self.update_input_types(build_config)

        # 注意：仅在非 tool_mode 下更新 action 选项。
        self._build_action_maps()
        # 注意：若已在 action 拉取阶段设置，避免重复覆盖。
        if "options" not in build_config.get("action_button", {}) or not build_config["action_button"]["options"]:
            build_config["action_button"]["options"] = [
                {"name": self.sanitize_action_name(action), "metadata": action} for action in self._actions_data
            ]
            logger.debug("Setting action options from main logic path")
        else:
            logger.debug("Action options already set, skipping duplicate setting")
        # 注意：仅在非 tool_mode 时显示 action 选择。
        if not current_tool_mode:
            build_config["action_button"]["show"] = True

        stored_connection_id = build_config.get("auth_link", {}).get("connection_id")
        active_connection_id = None

        if stored_connection_id:
            status = self._check_connection_status_by_id(stored_connection_id)
            if status == "ACTIVE":
                active_connection_id = stored_connection_id

        if not active_connection_id:
            active_connection = self._find_active_connection_for_app(self.app_name)
            if active_connection:
                active_connection_id, _ = active_connection
                if "auth_link" not in build_config:
                    build_config["auth_link"] = {}
                build_config["auth_link"]["connection_id"] = active_connection_id

        if active_connection_id:
            build_config["auth_link"]["value"] = "validated"
            build_config["auth_link"]["auth_tooltip"] = "Disconnect"
            build_config["action_button"]["helper_text"] = ""
            build_config["action_button"]["helper_text_metadata"] = {}

            # 注意：已连接时清理认证字段。
            schema = self._get_toolkit_schema()
            self._clear_auth_fields_from_schema(build_config, schema)

            # 注意：已连接时将 auth_mode 切换为 pill。
            scheme, _ = self._get_connection_auth_info(active_connection_id)
            if scheme:
                build_config.setdefault("auth_mode", {})
                build_config["auth_mode"]["value"] = scheme
                build_config["auth_mode"]["options"] = [scheme]
                build_config["auth_mode"]["show"] = False
                try:
                    pill = TabInput(
                        name="auth_mode",
                        display_name="Auth Mode",
                        options=[scheme],
                        value=scheme,
                    ).to_dict()
                    pill["show"] = True
                    build_config["auth_mode"] = pill
                except (TypeError, ValueError, AttributeError):
                    build_config["auth_mode"] = {
                        "name": "auth_mode",
                        "display_name": "Auth Mode",
                        "type": "tab",
                        "options": [scheme],
                        "value": scheme,
                        "show": True,
                    }
        elif stored_connection_id:
            status = self._check_connection_status_by_id(stored_connection_id)
            if status == "INITIATED":
                current_value = build_config.get("auth_link", {}).get("value")
                if not current_value or current_value == "connect":
                    build_config["auth_link"]["value"] = "connect"
                build_config["auth_link"]["auth_tooltip"] = "Connect"
                build_config["action_button"]["helper_text"] = "Please connect before selecting actions."
                build_config["action_button"]["helper_text_metadata"] = {"variant": "destructive"}
            else:
                # 注意：连接不存在或非 INITIATED/ACTIVE 状态。
                build_config["auth_link"]["value"] = "connect"
                build_config["auth_link"]["auth_tooltip"] = "Connect"
                build_config["action_button"]["helper_text"] = "Please connect before selecting actions."
                build_config["action_button"]["helper_text_metadata"] = {"variant": "destructive"}
        else:
            build_config["auth_link"]["value"] = "connect"
            build_config["auth_link"]["auth_tooltip"] = "Connect"
            build_config["action_button"]["helper_text"] = "Please connect before selecting actions."
            build_config["action_button"]["helper_text_metadata"] = {"variant": "destructive"}

        if self._is_tool_mode_enabled():
            build_config["action_button"]["show"] = False

        return self.update_input_types(build_config)

    def configure_tools(self, composio: Composio, limit: int | None = None) -> list[Tool]:
        """根据当前组件动作配置 Composio 工具列表。
        契约：返回带 `tags`/`metadata` 的 Tool 列表；失败语义为 SDK 异常透传。
        关键路径：拉取 tools → 设置 tags/metadata → 返回。
        决策：tag 使用原始 action 名。问题：避免展示名变化导致失配；方案：固定 slug；代价：UI 不够友好；重评：当 tag 与展示名强绑定时。
        """
        if limit is None:
            limit = 999

        tools = composio.tools.get(user_id=self.entity_id, toolkits=[self.app_name.lower()], limit=limit)
        configured_tools = []
        for tool in tools:
            # 注意：设置展示名并回退到清理后的名称。
            display_name = self._actions_data.get(tool.name, {}).get(
                "display_name", self._sanitized_names.get(tool.name, self._name_sanitizer.sub("-", tool.name))
            )
            # 实现：使用原始 action 名作为 tag。
            tool.tags = [tool.name]
            tool.metadata = {"display_name": display_name, "display_description": tool.description, "readonly": True}
            configured_tools.append(tool)
        return configured_tools

    async def _get_tools(self) -> list[Tool]:
        """获取工具列表并应用默认工具配置。"""
        composio = self._build_wrapper()
        self.set_default_tools()
        return self.configure_tools(composio)

    @property
    def enabled_tools(self):
        """返回应暴露给 agent 的 action tag 列表。

        契约：若设置了默认工具则返回默认列表；否则返回前 N 个 action。
        失败语义：action 未加载时会触发拉取。
        决策：限制默认数量防止压垮 agent。问题：工具过多影响提示；方案：default_tools_limit 限制；代价：可能遗漏；重评：当 agent 可动态检索时。

        """
        if not self._actions_data:
            self._populate_actions_data()

        if hasattr(self, "_default_tools") and self._default_tools:
            return list(self._default_tools)

        all_tools = list(self._actions_data.keys())
        limit = getattr(self, "default_tools_limit", 5)
        return all_tools[:limit]

    def execute_action(self):
        """执行当前选中的 Composio 工具。
        关键路径：1) 校验环境与 action 映射 2) 构建参数并执行 SDK 调用 3) 处理结果并返回。
        异常流：无效 action 或 SDK 失败时抛 `ValueError`；性能瓶颈为网络调用与参数序列化。
        排障入口：日志关键字 `execute` / `Failed to execute`。
        决策：保留字段重命名映射回原始参数。问题：保留字冲突；方案：执行前还原；代价：映射逻辑复杂；重评：当 SDK 支持命名空间参数时。
        """
        # 安全：Astra 云环境不支持该组件，提前失败避免远程调用。
        raise_error_if_astra_cloud_disable_component(disable_component_in_astra_cloud_msg)
        composio = self._build_wrapper()
        self._populate_actions_data()
        self._build_action_maps()

        display_name = (
            self.action_button[0]["name"]
            if isinstance(getattr(self, "action_button", None), list) and self.action_button
            else self.action_button
        )
        action_key = self._display_to_key_map.get(display_name)

        if not action_key:
            msg = f"Invalid action: {display_name}"
            raise ValueError(msg)

        try:
            arguments: dict[str, Any] = {}
            param_fields = self._actions_data.get(action_key, {}).get("action_fields", [])

            schema_dict = self._action_schemas.get(action_key, {})
            parameters_schema = schema_dict.get("input_parameters", {})
            schema_properties = parameters_schema.get("properties", {}) if parameters_schema else {}
            # 注意：required 可能为 None，需兜底为空列表。
            required_list = parameters_schema.get("required", []) if parameters_schema else []
            required_fields = set(required_list) if required_list is not None else set()

            for field in param_fields:
                if not hasattr(self, field):
                    continue
                value = getattr(self, field)

                # 注意：空值不参与参数构建。
                if value is None or value == "" or (isinstance(value, list) and len(value) == 0):
                    continue

                # 实现：读取字段 schema 以决定解析方式。
                prop_schema = schema_properties.get(field, {})

                # 注意：object/array 输入为字符串时尝试 JSON 解析。
                if isinstance(value, str) and prop_schema.get("type") in {"array", "object"}:
                    try:
                        value = json.loads(value)
                    except json.JSONDecodeError:
                        # 注意：解析失败时尝试逗号分隔的数组兜底。
                        if prop_schema.get("type") == "array":
                            value = [item.strip() for item in value.split(",") if item.strip() != ""]

                # 注意：可选字段仅在用户明确输入时才加入参数。
                if field not in required_fields:
                    # 注意：与 schema 默认值一致时忽略。
                    schema_default = prop_schema.get("default")
                    if value == schema_default:
                        continue

                if field in self._bool_variables:
                    value = bool(value)

                # 注意：字段重命名后需要映射回原始参数名。
                final_field_name = field
                # 实现：若加了 app 前缀且属于保留字，则还原。
                if field.startswith(f"{self.app_name}_"):
                    potential_original = field[len(self.app_name) + 1 :]  # Remove app_name prefix
                    if potential_original in self.RESERVED_ATTRIBUTES:
                        final_field_name = potential_original

                arguments[final_field_name] = value

            # 实现：读取 action 版本信息（若存在）。
            version = self._actions_data.get(action_key, {}).get("version")
            if version:
                logger.info(f"Executing {action_key} with version: {version}")

            # 实现：调用 SDK 执行，必要时带版本参数。
            execute_params = {
                "slug": action_key,
                "arguments": arguments,
                "user_id": self.entity_id,
            }

            # 注意：仅在有版本时传入。
            if version:
                execute_params["version"] = version

            result = composio.tools.execute(**execute_params)

            if isinstance(result, dict) and "successful" in result:
                if result["successful"]:
                    raw_data = result.get("data", result)
                    return self._apply_post_processor(action_key, raw_data)
                error_msg = result.get("error", "Tool execution failed")
                raise ValueError(error_msg)

        except ValueError as e:
            logger.error(f"Failed to execute {action_key}: {e}")
            raise

    def _apply_post_processor(self, action_key: str, raw_data: Any) -> Any:
        """对指定 action 应用后处理函数。"""
        if hasattr(self, "post_processors") and isinstance(self.post_processors, dict):
            processor_func = self.post_processors.get(action_key)
            if processor_func and callable(processor_func):
                try:
                    return processor_func(raw_data)
                except (TypeError, ValueError, KeyError) as e:
                    logger.error(f"Error in post-processor for {action_key}: {e} (Exception type: {type(e).__name__})")
                    return raw_data

        return raw_data

    def set_default_tools(self):
        """设置默认工具列表（由子类覆盖）。
        契约：子类应设置 `_default_tools`。
        关键路径：由子类实现。
        决策：留空由子类扩展。问题：不同工具默认集差异大；方案：子类覆写；代价：需要额外实现；重评：当存在全局默认策略时。
        """

    def _get_schema_field_names(
        self,
        schema: dict[str, Any] | None,
        mode: str,
        section_kind: str,
        bucket: str,
    ) -> set[str]:
        """从 schema 中提取指定 mode/section/bucket 的字段名集合。"""
        names: set[str] = set()
        if not schema:
            return names
        details = schema.get("auth_config_details") or schema.get("authConfigDetails") or []
        for item in details:
            if (item.get("mode") or item.get("auth_method")) != mode:
                continue
            fields = item.get("fields") or {}
            section = (
                fields.get(section_kind)
                or fields.get(
                    "authConfigCreation" if section_kind == "auth_config_creation" else "connectedAccountInitiation"
                )
                or {}
            )
            for entry in section.get(bucket, []) or []:
                name = entry.get("name") if isinstance(entry, dict) else None
                if name:
                    names.add(name)
        return names

    def _get_schema_required_entries(
        self,
        schema: dict[str, Any] | None,
        mode: str,
        section_kind: str,
    ) -> list[dict[str, Any]]:
        """从 schema 中提取指定 mode/section 的必填字段条目。"""
        if not schema:
            return []
        details = schema.get("auth_config_details") or schema.get("authConfigDetails") or []
        for item in details:
            if (item.get("mode") or item.get("auth_method")) != mode:
                continue
            fields = item.get("fields") or {}
            section = (
                fields.get(section_kind)
                or fields.get(
                    "authConfigCreation" if section_kind == "auth_config_creation" else "connectedAccountInitiation"
                )
                or {}
            )
            req = section.get("required", []) or []
            # 注意：仅保留 dict 结构的条目。
            return [entry for entry in req if isinstance(entry, dict)]
        return []

    def _hide_all_action_fields(self, build_config: dict) -> None:
        """隐藏并重置所有 action 参数字段。"""
        # 注意：先隐藏已知 action 字段。
        for fname in list(self._all_fields):
            if fname in build_config and isinstance(build_config[fname], dict):
                build_config[fname]["show"] = False
                build_config[fname]["value"] = "" if fname not in self._bool_variables else False
        # 注意：再隐藏其他疑似参数字段（排除保护名单）。
        protected = {
            # 注意：组件控制字段。
            "entity_id",
            "api_key",
            "auth_link",
            "action_button",
            "tool_mode",
            "auth_mode",
            "auth_mode_pill",
            "create_auth_config",
            # 注意：预置认证字段。
            "client_id",
            "client_secret",
            "verification_token",
            "redirect_uri",
            "authorization_url",
            "token_url",
            "api_key_field",
            "generic_api_key",
            "token",
            "access_token",
            "refresh_token",
            "username",
            "password",
            "domain",
            "base_url",
            "bearer_token",
            "authorization_code",
            "scopes",
            "subdomain",
            "instance_url",
            "tenant_id",
        }
        # 注意：加入保留属性，避免误隐藏。
        protected.update(self.RESERVED_ATTRIBUTES)
        # 注意：加入带 app 前缀的保留属性。
        for attr in self.RESERVED_ATTRIBUTES:
            protected.add(f"{self.app_name}_{attr}")
        # 注意：加入动态认证字段。
        protected.update(self._auth_dynamic_fields)
        # 注意：加入全局发现的认证字段。
        protected.update(self.__class__.get_all_auth_field_names())

        for key, cfg in list(build_config.items()):
            if key in protected:
                continue
            if isinstance(cfg, dict) and "show" in cfg:
                cfg["show"] = False
                if "value" in cfg:
                    cfg["value"] = ""
