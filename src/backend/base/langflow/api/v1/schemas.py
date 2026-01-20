"""
模块名称：API v1 数据模型

本模块集中定义 API v1 的请求/响应 Pydantic 模型与序列化规则。
主要功能：
- 统一接口入参与出参结构
- 提供流式/运行/任务等响应模型
- 约束序列化长度与敏感字段处理
设计背景：避免跨模块重复声明模型，保证序列化一致性。
注意事项：序列化使用全局长度限制，前端需处理截断字段。
"""

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from lfx.graph.schema import RunOutputs
from lfx.services.settings.base import Settings
from lfx.services.settings.feature_flags import FEATURE_FLAGS, FeatureFlags
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_serializer,
    field_validator,
    model_serializer,
)

from langflow.schema.dotdict import dotdict
from langflow.schema.graph import Tweaks
from langflow.schema.schema import InputType, OutputType, OutputValue
from langflow.serialization.serialization import get_max_items_length, get_max_text_length, serialize
from langflow.services.database.models.api_key.model import ApiKeyRead
from langflow.services.database.models.base import orjson_dumps
from langflow.services.database.models.flow.model import FlowCreate, FlowRead
from langflow.services.database.models.user.model import UserRead
from langflow.services.tracing.schema import Log


class BuildStatus(Enum):
    """构建状态枚举。"""

    SUCCESS = "success"
    FAILURE = "failure"
    STARTED = "started"
    IN_PROGRESS = "in_progress"


class TweaksRequest(BaseModel):
    """组件 `tweaks` 请求体。"""

    tweaks: dict[str, dict[str, Any]] | None = Field(default_factory=dict)


class UpdateTemplateRequest(BaseModel):
    """模板更新请求。"""

    template: dict


class TaskResponse(BaseModel):
    """任务响应模型。"""

    id: str | None = Field(None)
    href: str | None = Field(None)


class ProcessResponse(BaseModel):
    """处理任务响应模型。"""

    result: Any
    status: str | None = None
    task: TaskResponse | None = None
    session_id: str | None = None
    backend: str | None = None


class RunResponse(BaseModel):
    """流程运行响应模型。"""

    outputs: list[RunOutputs] | None = []
    session_id: str | None = None

    @model_serializer(mode="plain")
    def serialize(self):
        # 实现：将 `BaseModel` 输出序列化为字典，保持结构一致。
        serialized = {"session_id": self.session_id, "outputs": []}
        if self.outputs:
            serialized_outputs = []
            for output in self.outputs:
                if isinstance(output, BaseModel) and not isinstance(output, RunOutputs):
                    serialized_outputs.append(output.model_dump(exclude_none=True))
                else:
                    serialized_outputs.append(output)
            serialized["outputs"] = serialized_outputs
        return serialized


class PreloadResponse(BaseModel):
    """预加载响应模型。"""

    session_id: str | None = None
    is_clear: bool | None = None


class TaskStatusResponse(BaseModel):
    """任务状态响应模型。"""

    status: str
    result: Any | None = None


class ChatMessage(BaseModel):
    """聊天消息基础模型。"""

    is_bot: bool = False
    message: str | None | dict = None
    chat_key: str | None = Field(None, serialization_alias="chatKey")
    type: str = "human"


class ChatResponse(ChatMessage):
    """聊天响应模型（含中间步骤）。"""

    intermediate_steps: str

    type: str
    is_bot: bool = True
    files: list = []

    @field_validator("type")
    @classmethod
    def validate_message_type(cls, v):
        """校验消息类型枚举。"""
        if v not in {"start", "stream", "end", "error", "info", "file"}:
            msg = "type must be start, stream, end, error, info, or file"
            raise ValueError(msg)
        return v


class PromptResponse(ChatMessage):
    """提示词展示响应模型。"""

    prompt: str
    type: str = "prompt"
    is_bot: bool = True


class FileResponse(ChatMessage):
    """文件下载/展示响应模型。"""

    data: Any = None
    data_type: str
    type: str = "file"
    is_bot: bool = True

    @field_validator("data_type")
    @classmethod
    def validate_data_type(cls, v):
        """校验文件数据类型。"""
        if v not in {"image", "csv"}:
            msg = "data_type must be image or csv"
            raise ValueError(msg)
        return v


class FlowListCreate(BaseModel):
    """创建流程列表请求。"""

    flows: list[FlowCreate]


class FlowListIds(BaseModel):
    """流程 ID 列表请求。"""

    flow_ids: list[str]


class FlowListRead(BaseModel):
    """流程列表响应。"""

    flows: list[FlowRead]


class FlowListReadWithFolderName(BaseModel):
    """带文件夹名称的流程列表响应。"""

    flows: list[FlowRead]
    folder_name: str
    description: str


class InitResponse(BaseModel):
    """初始化响应，返回 `flowId`。"""

    flow_id: str = Field(serialization_alias="flowId")


class BuiltResponse(BaseModel):
    """构建结果响应。"""

    built: bool


class UploadFileResponse(BaseModel):
    """文件上传响应。"""

    flow_id: str = Field(serialization_alias="flowId")
    file_path: Path


class StreamData(BaseModel):
    """SSE 流式事件载体。"""

    event: str
    data: dict

    def __str__(self) -> str:
        """按 SSE 规范拼接事件字符串。"""
        return f"event: {self.event}\ndata: {orjson_dumps(self.data, indent_2=False)}\n\n"


class CustomComponentRequest(BaseModel):
    """自定义组件构建请求。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    code: str
    frontend_node: dict | None = None


class CustomComponentResponse(BaseModel):
    """自定义组件构建响应。"""

    data: dict
    type: str


class UpdateCustomComponentRequest(CustomComponentRequest):
    """自定义组件字段更新请求。"""

    field: str
    field_value: str | int | float | bool | dict | list | None = None
    template: dict
    tool_mode: bool = False

    def get_template(self):
        """将模板转换为 `dotdict` 便于下游读取。"""
        return dotdict(self.template)


class CustomComponentResponseError(BaseModel):
    """自定义组件错误响应。"""

    detail: str
    traceback: str


class ComponentListCreate(BaseModel):
    """组件列表创建请求。"""

    flows: list[FlowCreate]


class ComponentListRead(BaseModel):
    """组件列表读取响应。"""

    flows: list[FlowRead]


class UsersResponse(BaseModel):
    """用户列表响应。"""

    total_count: int
    users: list[UserRead]


class ApiKeyResponse(BaseModel):
    """API Key 详情响应（含展示字段）。"""

    id: str
    api_key: str
    name: str
    created_at: str
    last_used_at: str


class ApiKeysResponse(BaseModel):
    """API Key 列表响应。"""

    total_count: int
    user_id: UUID
    api_keys: list[ApiKeyRead]


class CreateApiKeyRequest(BaseModel):
    """创建 API Key 请求。"""

    name: str


class Token(BaseModel):
    """认证令牌响应。"""

    access_token: str
    refresh_token: str
    token_type: str


class ApiKeyCreateRequest(BaseModel):
    """前端存储 API Key 请求。"""

    api_key: str


class VerticesOrderResponse(BaseModel):
    """顶点执行顺序响应。"""

    ids: list[str]
    run_id: UUID
    vertices_to_run: list[str]


class ResultDataResponse(BaseModel):
    """构建结果响应，包含输出、日志与耗时信息。"""

    results: Any | None = Field(default_factory=dict)
    outputs: dict[str, OutputValue] = Field(default_factory=dict)
    logs: dict[str, list[Log]] = Field(default_factory=dict)
    message: Any | None = Field(default_factory=dict)
    artifacts: Any | None = Field(default_factory=dict)
    timedelta: float | None = None
    duration: str | None = None
    used_frozen_result: bool | None = False

    @field_serializer("results")
    @classmethod
    def serialize_results(cls, v):
        """序列化 `results` 并应用长度/条目截断。"""
        return serialize(v, max_length=get_max_text_length(), max_items=get_max_items_length())

    @model_serializer(mode="plain")
    def serialize_model(self) -> dict:
        """序列化整体响应并对大字段做截断。"""
        return {
            "results": self.serialize_results(self.results),
            "outputs": serialize(self.outputs, max_length=get_max_text_length(), max_items=get_max_items_length()),
            "logs": serialize(self.logs, max_length=get_max_text_length(), max_items=get_max_items_length()),
            "message": serialize(self.message, max_length=get_max_text_length(), max_items=get_max_items_length()),
            "artifacts": serialize(self.artifacts, max_length=get_max_text_length(), max_items=get_max_items_length()),
            "timedelta": self.timedelta,
            "duration": self.duration,
            "used_frozen_result": self.used_frozen_result,
        }


class VertexBuildResponse(BaseModel):
    """单个顶点构建响应。"""

    id: str | None = None
    inactivated_vertices: list[str] | None = None
    next_vertices_ids: list[str] | None = None
    top_level_vertices: list[str] | None = None
    valid: bool
    params: Any | None = Field(default_factory=dict)
    """参数内容（可能为 JSON）。"""
    data: ResultDataResponse
    """顶点结果数据，包含参数与输出。"""
    timestamp: datetime | None = Field(default_factory=lambda: datetime.now(timezone.utc))
    """构建时间戳（UTC）。"""

    @field_serializer("data")
    def serialize_data(self, data: ResultDataResponse) -> dict:
        """序列化 `ResultDataResponse` 并应用截断限制。"""
        # return serialize(data, max_length=get_max_text_length())  TODO: 是否安全？
        return serialize(data, max_length=get_max_text_length(), max_items=get_max_items_length())


class VerticesBuiltResponse(BaseModel):
    """批量顶点构建响应。"""

    vertices: list[VertexBuildResponse]


class SimplifiedAPIRequest(BaseModel):
    """简化运行请求（用于 API 直连调用）。"""

    input_value: str | None = Field(default=None, description="The input value")
    input_type: InputType | None = Field(default="chat", description="The input type")
    output_type: OutputType | None = Field(default="chat", description="The output type")
    output_component: str | None = Field(
        default="",
        description="If there are multiple output components, you can specify the component to get the output from.",
    )
    tweaks: Tweaks | None = Field(default=None, description="The tweaks")
    session_id: str | None = Field(default=None, description="The session id")


# 迁移上下文：对齐前端 `ReactFlow` JSON 结构。
class FlowDataRequest(BaseModel):
    """前端画布数据请求（节点/边/视口）。"""

    nodes: list[dict]
    edges: list[dict]
    viewport: dict | None = None


class ConfigResponse(BaseModel):
    """应用配置响应（含特性开关与阈值）。"""

    feature_flags: FeatureFlags
    serialization_max_items_length: int
    serialization_max_text_length: int
    frontend_timeout: int
    auto_saving: bool
    auto_saving_interval: int
    health_check_max_retries: int
    max_file_size_upload: int
    webhook_polling_interval: int
    public_flow_cleanup_interval: int
    public_flow_expiration: int
    event_delivery: Literal["polling", "streaming", "direct"]
    webhook_auth_enable: bool
    voice_mode_available: bool
    default_folder_name: str
    hide_getting_started_progress: bool

    @classmethod
    def from_settings(cls, settings: Settings, auth_settings) -> "ConfigResponse":
        """从系统配置与认证配置构建 `ConfigResponse`。"""
        import os

        from langflow.services.database.models.folder.constants import DEFAULT_FOLDER_NAME

        return cls(
            feature_flags=FEATURE_FLAGS,
            serialization_max_items_length=settings.max_items_length,
            serialization_max_text_length=settings.max_text_length,
            frontend_timeout=settings.frontend_timeout,
            auto_saving=settings.auto_saving,
            auto_saving_interval=settings.auto_saving_interval,
            health_check_max_retries=settings.health_check_max_retries,
            max_file_size_upload=settings.max_file_size_upload,
            webhook_polling_interval=settings.webhook_polling_interval,
            public_flow_cleanup_interval=settings.public_flow_cleanup_interval,
            public_flow_expiration=settings.public_flow_expiration,
            event_delivery=settings.event_delivery,
            voice_mode_available=settings.voice_mode_available,
            webhook_auth_enable=auth_settings.WEBHOOK_AUTH_ENABLE,
            default_folder_name=DEFAULT_FOLDER_NAME,
            hide_getting_started_progress=os.getenv("HIDE_GETTING_STARTED_PROGRESS", "").lower() == "true",
        )


class CancelFlowResponse(BaseModel):
    """流程构建取消响应。"""

    success: bool
    message: str


class AuthSettings(BaseModel):
    """MCP 认证设置模型。"""

    auth_type: Literal["none", "apikey", "oauth"] = "none"
    oauth_host: str | None = None
    oauth_port: str | None = None
    oauth_server_url: str | None = None
    oauth_callback_path: str | None = None  # Deprecated: use oauth_callback_url instead
    oauth_callback_url: str | None = None
    oauth_client_id: str | None = None
    oauth_client_secret: SecretStr | None = None
    oauth_auth_url: str | None = None
    oauth_token_url: str | None = None
    oauth_mcp_scope: str | None = None
    oauth_provider_scope: str | None = None

    def model_post_init(self, __context, /) -> None:
        """兼容旧字段 `oauth_callback_path` 并归一化为 `oauth_callback_url`。"""
        # 注意：仅在新字段为空时回填旧字段。
        if self.oauth_callback_url is None and self.oauth_callback_path is not None:
            self.oauth_callback_url = self.oauth_callback_path
        # 注意：两者同时存在时以 `oauth_callback_url` 为准。


class MCPSettings(BaseModel):
    """流程级 MCP 设置模型。"""

    id: UUID
    mcp_enabled: bool | None = None
    action_name: str | None = None
    action_description: str | None = None
    name: str | None = None
    description: str | None = None


class MCPProjectUpdateRequest(BaseModel):
    """更新 MCP 项目与认证设置的请求模型。"""

    settings: list[MCPSettings]
    auth_settings: AuthSettings | None = None


class MCPProjectResponse(BaseModel):
    """MCP 项目工具列表响应（含认证设置）。"""

    tools: list[MCPSettings]
    auth_settings: AuthSettings | None = None


class ComposerUrlResponse(BaseModel):
    """MCP Composer 连接信息响应。"""

    project_id: str
    uses_composer: bool
    streamable_http_url: str | None = None
    legacy_sse_url: str | None = None
    error_message: str | None = None


class MCPInstallRequest(BaseModel):
    """MCP 客户端安装请求。"""

    client: str
    transport: Literal["sse", "streamablehttp"] | None = None
