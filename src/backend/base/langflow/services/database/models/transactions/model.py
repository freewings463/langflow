"""
模块名称：交易日志模型

本模块定义节点运行交易日志的存储与脱敏规则。
主要功能包括：敏感字段脱敏、输入输出序列化与日志响应模型。

关键组件：`TransactionTable` / `sanitize_data`
设计背景：在记录调试数据时保护敏感信息并限制字段大小。
使用场景：执行日志记录、调试与审计视图。
注意事项：敏感键会被脱敏，部分字段会被排除。
"""

import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from pydantic import field_serializer, field_validator
from sqlmodel import JSON, Column, Field, SQLModel

from langflow.serialization.serialization import get_max_items_length, get_max_text_length, serialize

# 注意：敏感字段键名清单，值将被脱敏。
# 注意：正则使用全匹配避免误匹配（如 `max_tokens`）。
SENSITIVE_KEY_NAMES = frozenset(
    {
        "api_key",
        "api-key",
        "apikey",
        "password",
        "passwd",
        "secret",
        "token",
        "auth_token",
        "access_token",
        "api_token",
        "bearer_token",
        "credential",
        "credentials",
        "auth",
        "authorization",
        "bearer",
        "private_key",
        "private-key",
        "access_key",
        "access-key",
        "openai_api_key",
        "anthropic_api_key",
    }
)

# 注意：用于识别敏感后缀的正则。
SENSITIVE_KEYS_PATTERN = re.compile(
    r".*[_-]?(api[_-]?key|password|secret|token|credential|auth|bearer|private[_-]?key|access[_-]?key)$",
    re.IGNORECASE,
)

# 注意：完全排除的字段键名。
EXCLUDED_KEYS = frozenset({"code"})

# 注意：部分脱敏最小长度（保留前 4 后 4）。
MIN_LENGTH_FOR_PARTIAL_MASK = 12


def _mask_sensitive_value(value: str) -> str:
    """脱敏字符串，仅保留前 4 后 4 字符。

    契约：
    - 输入：敏感字符串 `value`。
    - 输出：脱敏后的字符串。
    - 失败语义：无显式抛错。
    """
    if len(value) <= MIN_LENGTH_FOR_PARTIAL_MASK:
        return "***REDACTED***"
    return f"{value[:4]}...{value[-4:]}"


def _is_sensitive_key(key: str) -> bool:
    """判断键名是否需要脱敏。"""
    key_lower = key.lower()
    # 注意：先做精确匹配以减少正则开销。
    if key_lower in SENSITIVE_KEY_NAMES:
        return True
    # 注意：再匹配敏感后缀正则。
    return bool(SENSITIVE_KEYS_PATTERN.match(key_lower))


def _sanitize_dict(data: dict[str, Any]) -> dict[str, Any]:
    """递归脱敏字典。"""
    result: dict[str, Any] = {}
    for key, value in data.items():
        if key in EXCLUDED_KEYS:
            continue
        if _is_sensitive_key(key):
            if isinstance(value, str) and value:
                result[key] = _mask_sensitive_value(value)
            else:
                result[key] = "***REDACTED***"
        elif isinstance(value, dict):
            result[key] = _sanitize_dict(value)
        elif isinstance(value, list):
            result[key] = _sanitize_list(value)
        else:
            result[key] = value
    return result


def _sanitize_list(data: list[Any]) -> list[Any]:
    """递归脱敏列表。"""
    result: list[Any] = []
    for item in data:
        if isinstance(item, dict):
            result.append(_sanitize_dict(item))
        elif isinstance(item, list):
            result.append(_sanitize_list(item))
        else:
            result.append(item)
    return result


def sanitize_data(data: dict[str, Any] | None) -> dict[str, Any] | None:
    """对输入数据执行脱敏与排除策略。

    契约：
    - 输入：`dict` 或 `None`。
    - 输出：脱敏后的字典或 `None`。
    - 副作用：无。
    - 失败语义：非 `dict` 时原样返回。

    决策：非字典输入直接返回。
    问题：调用方可能传入非结构化数据。
    方案：仅处理 `dict`，其余保持原样。
    代价：非字典结构无法脱敏。
    重评：当需要处理更多类型时扩展分支。
    """
    if data is None:
        return None
    if not isinstance(data, dict):
        return data
    return _sanitize_dict(data)


class TransactionBase(SQLModel):
    """交易日志基础模型。

    契约：
    - 字段：`timestamp`/`vertex_id`/`inputs`/`outputs`/`status` 等。
    - 用途：用于表模型与响应模型复用。
    - 失败语义：字段校验失败抛异常。

    关键路径：初始化时对 `inputs`/`outputs` 进行脱敏。

    决策：在初始化时对 `inputs`/`outputs` 进行脱敏。
    问题：日志中可能包含密钥与敏感信息。
    方案：构造时调用 `sanitize_data`。
    代价：原始明文数据不可恢复。
    重评：当引入安全存储隔离时改为存储加密版本。
    """
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    vertex_id: str = Field(nullable=False)
    target_id: str | None = Field(default=None)
    inputs: dict | None = Field(default=None, sa_column=Column(JSON))
    outputs: dict | None = Field(default=None, sa_column=Column(JSON))
    status: str = Field(nullable=False)
    error: str | None = Field(default=None)
    flow_id: UUID = Field()

    # 注意：允许 `JSON` 列类型的任意对象。
    class Config:
        arbitrary_types_allowed = True

    def __init__(self, **data):
        # 注意：写入前对输入输出进行脱敏处理。
        if "inputs" in data and isinstance(data["inputs"], dict):
            data["inputs"] = sanitize_data(data["inputs"])
        if "outputs" in data and isinstance(data["outputs"], dict):
            data["outputs"] = sanitize_data(data["outputs"])
        super().__init__(**data)

    @field_validator("flow_id", mode="before")
    @classmethod
    def validate_flow_id(cls, value):
        """将 `flow_id` 字符串转为 `UUID`。"""
        if value is None:
            return value
        if isinstance(value, str):
            value = UUID(value)
        return value

    @field_serializer("inputs")
    def serialize_inputs(self, data) -> dict:
        """序列化 `inputs` 并限制大小。"""
        sanitized = sanitize_data(data)
        return serialize(sanitized, max_length=get_max_text_length(), max_items=get_max_items_length())

    @field_serializer("outputs")
    def serialize_outputs(self, data) -> dict:
        """序列化 `outputs` 并限制大小。"""
        sanitized = sanitize_data(data)
        return serialize(sanitized, max_length=get_max_text_length(), max_items=get_max_items_length())


class TransactionTable(TransactionBase, table=True):  # type: ignore[call-arg]
    """交易日志表模型。

    契约：
    - 主键：`id`。
    - 关键字段：`flow_id`/`vertex_id`/`status`。
    - 用途：持久化交易记录。
    - 失败语义：数据库约束异常透传。

    关键路径：字段序列化由 `TransactionBase` 提供。

    决策：以独立 `id` 作为主键。
    问题：同一节点会产生多次交易记录。
    方案：每次交易记录独立存储。
    代价：记录数量随执行次数增长。
    重评：当仅需最新状态时改为覆盖更新。
    """
    __tablename__ = "transaction"
    id: UUID | None = Field(default_factory=uuid4, primary_key=True)


class TransactionReadResponse(TransactionBase):
    """交易日志读取响应模型。

    契约：
    - 输出字段：`transaction_id` 别名映射到 `id`。
    - 用途：对外接口返回结构。
    - 失败语义：字段校验失败抛异常。

    关键路径：使用 `alias` 映射字段并由模型校验。

    决策：使用 `transaction_id` 别名提升可读性。
    问题：外部 API 需要语义明确的字段名。
    方案：通过 `alias` 提供友好字段名。
    代价：内部字段名与外部字段名不一致。
    重评：当 API 字段统一后移除别名。
    """
    id: UUID = Field(alias="transaction_id")
    flow_id: UUID


class TransactionLogsResponse(SQLModel):
    """日志视图响应模型（不含 `error` 与 `flow_id`）。

    契约：
    - 输出字段：包含日志关键字段与脱敏输入输出。
    - 用途：日志视图展示。
    - 失败语义：字段校验失败抛异常。

    关键路径：序列化 `inputs`/`outputs` 时应用脱敏与限长。

    决策：排除 `error` 与 `flow_id` 以减小负载。
    问题：日志视图只需核心字段。
    方案：定义独立响应模型。
    代价：详情视图需另行查询。
    重评：当日志视图需要完整信息时补回字段。
    """

    model_config = {"populate_by_name": True, "from_attributes": True}

    id: UUID
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    vertex_id: str = Field(nullable=False)
    target_id: str | None = Field(default=None)
    inputs: dict | None = Field(default=None)
    outputs: dict | None = Field(default=None)
    status: str = Field(nullable=False)

    @field_serializer("inputs")
    def serialize_inputs(self, data) -> dict:
        """序列化 `inputs` 并限制大小。"""
        sanitized = sanitize_data(data)
        return serialize(sanitized, max_length=get_max_text_length(), max_items=get_max_items_length())

    @field_serializer("outputs")
    def serialize_outputs(self, data) -> dict:
        """序列化 `outputs` 并限制大小。"""
        sanitized = sanitize_data(data)
        return serialize(sanitized, max_length=get_max_text_length(), max_items=get_max_items_length())
