"""
模块名称：消息数据模型

本模块定义消息的数据库模型与读写模型。
主要功能包括：时间戳序列化、文件列表归一化与属性字段序列化。

关键组件：`MessageTable` / `MessageBase` / `MessageRead`
设计背景：统一消息持久化结构，保证时区与属性字段一致。
使用场景：消息记录、聊天历史与审计展示。
注意事项：保存与读取过程中会补齐 `UTC` 时区。
"""

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Annotated
from uuid import UUID, uuid4

from pydantic import ConfigDict, field_serializer, field_validator
from sqlalchemy import Text
from sqlmodel import JSON, Column, Field, SQLModel

from langflow.schema.content_block import ContentBlock
from langflow.schema.properties import Properties
from langflow.schema.validators import str_to_timestamp_validator

if TYPE_CHECKING:
    from langflow.schema.message import Message


class MessageBase(SQLModel):
    """消息基础字段模型。

    契约：
    - 字段：`timestamp`/`sender`/`session_id`/`text` 等。
    - 用途：供创建/读取/更新模型复用。
    - 失败语义：字段校验失败抛异常。

    关键路径：字段归一化在校验器中执行。

    决策：统一在模型层处理时间与列表字段归一化。
    问题：消息数据来源多样，字段格式易不一致。
    方案：使用 `field_validator` 进行格式化。
    代价：模型层承担更多数据清洗职责。
    重评：当上游保证字段一致性时可简化校验。
    """
    timestamp: Annotated[datetime, str_to_timestamp_validator] = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    sender: str
    sender_name: str
    session_id: str
    context_id: str | None = Field(default=None)
    text: str = Field(sa_column=Column(Text))
    files: list[str] = Field(default_factory=list)
    error: bool = Field(default=False)
    edit: bool = Field(default=False)

    properties: Properties = Field(default_factory=Properties)
    category: str = Field(default="message")
    content_blocks: list[ContentBlock] = Field(default_factory=list)

    @field_serializer("timestamp")
    def serialize_timestamp(self, value):
        """将 `timestamp` 序列化为字符串。"""
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.strftime("%Y-%m-%d %H:%M:%S %Z")
        if isinstance(value, str):
            # 注意：字符串时间统一转为 `UTC`。
            value = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
            return value.strftime("%Y-%m-%d %H:%M:%S %Z")
        return value

    @field_validator("files", mode="before")
    @classmethod
    def validate_files(cls, value):
        """保证 `files` 为列表。"""
        if not value:
            value = []
        return value

    @field_validator("session_id", mode="before")
    @classmethod
    def validate_session_id(cls, value):
        """将 `session_id` 规范化为字符串。"""
        if isinstance(value, UUID):
            value = str(value)
        return value

    @classmethod
    def from_message(cls, message: "Message", flow_id: str | UUID | None = None):
        """从 `Message` 对象构建模型。

        契约：
        - 输入：`message` 与可选 `flow_id`。
        - 输出：`MessageBase` 实例。
        - 副作用：可能重写 `message.files`。
        - 失败语义：缺失必填字段或 `flow_id` 不合法时抛 `ValueError`。

        关键路径（三步）：
        1) 校验必填字段并规范化文件路径。
        2) 解析时间戳与 `flow_id`。
        3) 组装属性与内容块并返回模型。

        决策：对 `files` 中路径进行会话前缀裁剪。
        问题：前端上传路径包含会话前缀，保存时需统一。
        方案：按 `session_id` 截断并保留相对路径。
        代价：原始路径信息不可逆。
        重评：当存储系统提供标准化路径时移除裁剪逻辑。
        """
        # 注意：先校验必填字段，避免生成不完整记录。
        if message.text is None or not message.sender or not message.sender_name:
            msg = "The message does not have the required fields (text, sender, sender_name)."
            raise ValueError(msg)
        if message.files:
            image_paths = []
            for file in message.files:
                if hasattr(file, "path") and hasattr(file, "url") and file.path:
                    session_id = message.session_id
                    if session_id and str(session_id) in file.path:
                        parts = file.path.split(str(session_id))
                        if len(parts) > 1:
                            image_paths.append(f"{session_id}{parts[1]}")
                        else:
                            image_paths.append(file.path)
                    else:
                        image_paths.append(file.path)
            if image_paths:
                message.files = image_paths

        if isinstance(message.timestamp, str):
            # 注意：支持 `YYYY-MM-DD HH:MM:SS UTC` 与 `ISO` 格式时间。
            try:
                timestamp = datetime.strptime(message.timestamp, "%Y-%m-%d %H:%M:%S %Z").replace(tzinfo=timezone.utc)
            except ValueError:
                # 注意：格式不匹配时回退到 `ISO` 解析。
                timestamp = datetime.fromisoformat(message.timestamp).replace(tzinfo=timezone.utc)
        else:
            timestamp = message.timestamp
        if not flow_id and message.flow_id:
            flow_id = message.flow_id
        # 注意：非字符串文本（如异步迭代器）统一转为空字符串。
        message_text = "" if not isinstance(message.text, str) else message.text

        properties = (
            message.properties.model_dump_json()
            if hasattr(message.properties, "model_dump_json")
            else message.properties
        )
        content_blocks = []
        for content_block in message.content_blocks or []:
            content = content_block.model_dump_json() if hasattr(content_block, "model_dump_json") else content_block
            content_blocks.append(content)

        if isinstance(flow_id, str):
            try:
                flow_id = UUID(flow_id)
            except ValueError as exc:
                msg = f"Flow ID {flow_id} is not a valid UUID"
                raise ValueError(msg) from exc

        return cls(
            sender=message.sender,
            sender_name=message.sender_name,
            text=message_text,
            session_id=message.session_id,
            context_id=message.context_id,
            files=message.files or [],
            timestamp=timestamp,
            flow_id=flow_id,
            properties=properties,
            category=message.category,
            content_blocks=content_blocks,
        )


class MessageTable(MessageBase, table=True):  # type: ignore[call-arg]
    """消息数据库表模型。

    契约：
    - 主键：`id`。
    - 关键字段：`flow_id`/`properties`/`content_blocks`。
    - 副作用：序列化/反序列化 `JSON` 字段。
    - 失败语义：字段解析失败抛异常。

    关键路径：`properties` 与 `content_blocks` 在序列化器中处理。

    决策：将 `properties` 与 `content_blocks` 存储为 `JSON`。
    问题：消息属性与内容块结构不固定。
    方案：使用 `JSON` 列并在模型层序列化。
    代价：缺乏强结构约束。
    重评：当结构稳定后可拆为结构化表。
    """
    model_config = ConfigDict(validate_assignment=True, arbitrary_types_allowed=True)
    __tablename__ = "message"
    id: UUID = Field(default_factory=uuid4, primary_key=True)

    flow_id: UUID | None = Field(default=None)
    files: list[str] = Field(sa_column=Column(JSON))
    properties: dict | Properties = Field(default_factory=lambda: Properties().model_dump(), sa_column=Column(JSON))  # type: ignore[assignment]
    category: str = Field(sa_column=Column(Text))
    content_blocks: list[dict | ContentBlock] = Field(default_factory=list, sa_column=Column(JSON))  # type: ignore[assignment]

    # 注意：通过校验器补齐 `timestamp` 时区信息。

    @field_validator("flow_id", mode="before")
    @classmethod
    def validate_flow_id(cls, value):
        """将 `flow_id` 字符串转为 `UUID`。"""
        if value is None:
            return value
        if isinstance(value, str):
            value = UUID(value)
        return value

    @field_validator("properties", "content_blocks", mode="before")
    @classmethod
    def validate_properties_or_content_blocks(cls, value):
        """统一 `properties` 与 `content_blocks` 的输入格式。"""
        if isinstance(value, list):
            return [cls.validate_properties_or_content_blocks(item) for item in value]
        if hasattr(value, "model_dump"):
            return value.model_dump()
        if isinstance(value, str):
            return json.loads(value)
        return value

    @field_serializer("properties", "content_blocks")
    @classmethod
    def serialize_properties_or_content_blocks(cls, value) -> dict | list[dict]:
        """统一序列化 `properties` 与 `content_blocks`。"""
        if isinstance(value, list):
            return [cls.serialize_properties_or_content_blocks(item) for item in value]
        if hasattr(value, "model_dump"):
            return value.model_dump()
        if isinstance(value, str):
            return json.loads(value)
        return value


class MessageRead(MessageBase):
    """消息读取模型。

    契约：
    - 输出字段：`id` 与 `flow_id` 等基础信息。
    - 用途：消息列表与详情读取。
    - 失败语义：字段校验失败抛异常。

    关键路径：字段校验与序列化由 `Pydantic` 执行。

    决策：读取模型继承 `MessageBase` 保持字段一致。
    问题：读取与创建字段高度一致，重复定义易出错。
    方案：继承基类并补充只读字段。
    代价：变更基类会影响读取模型。
    重评：当读取字段差异扩大时拆分独立模型。
    """
    id: UUID
    flow_id: UUID | None = Field()


class MessageCreate(MessageBase):
    """消息创建模型。

    契约：
    - 输入字段：继承 `MessageBase`。
    - 用途：创建新消息记录。
    - 失败语义：字段校验失败抛异常。

    关键路径：字段校验由 `Pydantic` 执行并生成模型实例。

    决策：复用基类字段避免重复定义。
    问题：创建与读取字段高度重合。
    方案：直接继承基类。
    代价：创建模型无法限制某些字段。
    重评：当需要限制可写字段时改为显式字段列表。
    """
    pass


class MessageUpdate(SQLModel):
    """消息更新输入模型。

    契约：
    - 输入字段：均为可选。
    - 用途：局部更新消息属性。
    - 失败语义：字段校验失败抛异常。

    关键路径：仅对传入字段进行校验与序列化。

    决策：所有字段可选以支持部分更新。
    问题：消息编辑通常只修改少数字段。
    方案：将更新字段全部设为可选。
    代价：调用方需区分 `None` 与不更新。
    重评：当需要强制字段更新时调整为必填。
    """
    text: str | None = None
    sender: str | None = None
    sender_name: str | None = None
    session_id: str | None = None
    context_id: str | None = None
    files: list[str] | None = None
    edit: bool | None = None
    error: bool | None = None
    properties: Properties | None = None
