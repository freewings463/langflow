"""
模块名称：`Flow` 数据模型

本模块定义 `Flow` 的数据库模型、读写模型与校验规则。
主要功能包括：字段校验、图数据序列化、访问类型与组件标识管理。

关键组件：`Flow` / `FlowBase` / `FlowUpdate` / `FlowHeader`
设计背景：统一流程配置的持久化结构与校验逻辑。
使用场景：流程创建、更新、读取与鉴权展示。
注意事项：`endpoint_name` 与 `user_id` 存在唯一约束。
"""

import re
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Optional
from uuid import UUID, uuid4

import emoji
from emoji import purely_emoji
from fastapi import HTTPException, status
from lfx.log.logger import logger
from pydantic import BaseModel, ValidationInfo, field_serializer, field_validator
from sqlalchemy import Enum as SQLEnum
from sqlalchemy import Text, UniqueConstraint, text
from sqlmodel import JSON, Column, Field, Relationship, SQLModel

from langflow.schema.data import Data

if TYPE_CHECKING:
    from langflow.services.database.models.folder.model import Folder
    from langflow.services.database.models.user.model import User

HEX_COLOR_LENGTH = 7


class AccessTypeEnum(str, Enum):
    """`Flow` 访问级别枚举。

    契约：
    - 取值：`PRIVATE`/`PUBLIC`。
    - 用途：标识流程可见性。
    - 失败语义：非法取值由枚举/字段校验抛出。
    """
    PRIVATE = "PRIVATE"
    PUBLIC = "PUBLIC"


class FlowBase(SQLModel):
    """`Flow` 基础字段模型。

    契约：
    - 字段：名称、描述、图数据与访问控制字段等。
    - 用途：供创建/读取/更新模型复用。
    - 失败语义：字段校验失败抛 `ValueError` 或 `HTTPException`。

    关键路径：校验器在模型构建时执行。

    决策：在基础模型中集中字段校验。
    问题：各类模型重复校验逻辑易产生偏差。
    方案：在基类统一 `field_validator`。
    代价：基类变更影响所有派生模型。
    重评：当校验规则分化时拆分基类。
    """
    # 注意：抑制迁移阶段的删除行确认告警。
    __mapper_args__ = {"confirm_deleted_rows": False}

    name: str = Field(index=True)
    description: str | None = Field(default=None, sa_column=Column(Text, index=True, nullable=True))
    icon: str | None = Field(default=None, nullable=True)
    icon_bg_color: str | None = Field(default=None, nullable=True)
    gradient: str | None = Field(default=None, nullable=True)
    data: dict | None = Field(default=None, nullable=True)
    is_component: bool | None = Field(default=False, nullable=True)
    updated_at: datetime | None = Field(default_factory=lambda: datetime.now(timezone.utc), nullable=True)
    webhook: bool | None = Field(default=False, nullable=True, description="Can be used on the webhook endpoint")
    endpoint_name: str | None = Field(default=None, nullable=True, index=True)
    tags: list[str] | None = None
    locked: bool | None = Field(default=False, nullable=True)
    mcp_enabled: bool | None = Field(default=False, nullable=True, description="Can be exposed in the MCP server")
    action_name: str | None = Field(
        default=None, nullable=True, description="The name of the action associated with the flow"
    )
    action_description: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
        description="The description of the action associated with the flow",
    )
    access_type: AccessTypeEnum = Field(
        default=AccessTypeEnum.PRIVATE,
        sa_column=Column(
            SQLEnum(
                AccessTypeEnum,
                name="access_type_enum",
                values_callable=lambda enum: [member.value for member in enum],
            ),
            nullable=False,
            server_default=text("'PRIVATE'"),
        ),
    )

    @field_validator("endpoint_name")
    @classmethod
    def validate_endpoint_name(cls, v):
        """校验 `endpoint_name` 合法性。

        契约：
        - 输入：`endpoint_name` 字符串或 `None`。
        - 输出：合法值或 `None`。
        - 失败语义：不合法时抛 `HTTPException(422)`。
        """
        if v is not None:
            if not isinstance(v, str):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Endpoint name must be a string",
                )
            if not re.match(r"^[a-zA-Z0-9_-]+$", v):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Endpoint name must contain only letters, numbers, hyphens, and underscores",
                )
        return v

    @field_validator("icon_bg_color")
    @classmethod
    def validate_icon_bg_color(cls, v):
        """校验图标背景色格式。

        契约：
        - 输入：颜色字符串或 `None`。
        - 输出：合法颜色值。
        - 失败语义：格式不合法抛 `ValueError`。
        """
        if v is not None and not isinstance(v, str):
            msg = "Icon background color must be a string"
            raise ValueError(msg)
        # 注意：颜色必须以 `#` 开头。
        if v and not v.startswith("#"):
            msg = "Icon background color must start with #"
            raise ValueError(msg)

        # 注意：长度必须为 `#RRGGBB` 的 7 位。
        if v and len(v) != HEX_COLOR_LENGTH:
            msg = "Icon background color must be 7 characters long"
            raise ValueError(msg)
        return v

    @field_validator("icon")
    @classmethod
    def validate_icon_atr(cls, v):
        """校验图标字段的表情或图标格式。

        契约：
        - 输入：图标字符串或 `None`。
        - 输出：合法图标值。
        - 失败语义：非法格式抛 `ValueError`。
        """
        if v is None:
            return v
        # 注意：支持 `:emoji_name:` 语法并使用 `emoji` 库解析。

        if not v.startswith(":") and not v.endswith(":"):
            return v
        if not v.startswith(":") or not v.endswith(":"):
            # 注意：表情名称必须同时包含首尾冒号。
            msg = f"Invalid emoji. {v} is not a valid emoji."
            raise ValueError(msg)

        emoji_value = emoji.emojize(v, variant="emoji_type")
        if v == emoji_value:
            logger.warning(f"Invalid emoji. {v} is not a valid emoji.")
        icon = emoji_value

        if purely_emoji(icon):
            # 注意：确认返回值为纯表情。
            return icon
        # 注意：非表情时按图标名称规则校验。
        if v is not None and not isinstance(v, str):
            msg = "Icon must be a string"
            raise ValueError(msg)
        # 注意：图标名称必须为小写字母与连字符。
        if v and not v.islower():
            msg = "Icon must be lowercase"
            raise ValueError(msg)
        if v and not v.replace("-", "").isalpha():
            msg = "Icon must contain only letters and hyphens"
            raise ValueError(msg)
        return v

    @field_validator("data")
    @classmethod
    def validate_json(cls, v):
        """校验 `Flow` 图数据结构。

        契约：
        - 输入：`dict` 或 `None`。
        - 输出：合法数据字典。
        - 失败语义：缺少 `nodes`/`edges` 时抛 `ValueError`。
        """
        if not v:
            return v
        if not isinstance(v, dict):
            msg = "Flow must be a valid JSON"
            raise ValueError(msg)  # noqa: TRY004

        # 注意：图数据必须包含 `nodes` 与 `edges`。
        if "nodes" not in v:
            msg = "Flow must have nodes"
            raise ValueError(msg)
        if "edges" not in v:
            msg = "Flow must have edges"
            raise ValueError(msg)

        return v

    @field_serializer("updated_at")
    def serialize_datetime(self, value):
        """序列化 `updated_at` 为 `ISO-8601` 字符串。"""
        if isinstance(value, datetime):
            # 注意：去除微秒并补齐时区信息。
            value = value.replace(microsecond=0)
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.isoformat()
        return value

    @field_validator("updated_at", mode="before")
    @classmethod
    def validate_dt(cls, v):
        """将字符串时间转为 `datetime`。"""
        if v is None:
            return v
        if isinstance(v, datetime):
            return v

        return datetime.fromisoformat(v)


class Flow(FlowBase, table=True):  # type: ignore[call-arg]
    """`Flow` 数据库表模型。

    契约：
    - 主键：`id`。
    - 关键字段：`user_id`/`folder_id`/`data`/`endpoint_name`。
    - 约束：`user_id+name`、`user_id+endpoint_name` 唯一。
    - 失败语义：违反唯一约束时抛数据库异常。

    关键路径：图数据写入 `JSON` 列并由 `to_data` 读取。

    决策：将 `data` 存为 `JSON` 列。
    问题：流程图结构需要可查询与可迁移存储。
    方案：使用 `JSON` 列保存图数据。
    代价：部分数据库对 `JSON` 索引支持有限。
    重评：当需要高性能查询时改为拆表结构化存储。
    """
    id: UUID = Field(default_factory=uuid4, primary_key=True, unique=True)
    data: dict | None = Field(default=None, sa_column=Column(JSON))
    user_id: UUID | None = Field(index=True, foreign_key="user.id", nullable=True)
    user: "User" = Relationship(back_populates="flows")
    icon: str | None = Field(default=None, nullable=True)
    tags: list[str] | None = Field(sa_column=Column(JSON), default=[])
    locked: bool | None = Field(default=False, nullable=True)
    folder_id: UUID | None = Field(default=None, foreign_key="folder.id", nullable=True, index=True)
    fs_path: str | None = Field(default=None, nullable=True)
    folder: Optional["Folder"] = Relationship(back_populates="flows")

    def to_data(self):
        """将模型转换为 `Data` 对象。

        契约：
        - 输入：无。
        - 输出：`Data`，包含核心字段。
        - 副作用：无。
        - 失败语义：序列化异常透传。

        关键路径：
        1) `model_dump` 序列化。
        2) 选取核心字段并构造 `Data`。
        """
        serialized = self.model_dump()
        data = {
            "id": serialized.pop("id"),
            "data": serialized.pop("data"),
            "name": serialized.pop("name"),
            "description": serialized.pop("description"),
            "updated_at": serialized.pop("updated_at"),
            "folder_id": serialized.pop("folder_id"),
        }
        return Data(data=data)

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="unique_flow_name"),
        UniqueConstraint("user_id", "endpoint_name", name="unique_flow_endpoint_name"),
    )


class FlowCreate(FlowBase):
    """`Flow` 创建输入模型。

    契约：
    - 输入字段：`user_id`/`folder_id`/`fs_path` 等。
    - 输出：用于创建流程的结构化数据。
    - 失败语义：字段校验失败抛异常。

    关键路径：字段校验由 `SQLModel` 执行。

    决策：允许可选 `folder_id` 与 `fs_path`。
    问题：流程可能来自文件系统或根目录。
    方案：提供可选字段并保持兼容。
    代价：调用方需自行保证一致性。
    重评：当存储路径统一后可移除 `fs_path`。
    """
    user_id: UUID | None = None
    folder_id: UUID | None = None
    fs_path: str | None = None


class FlowRead(FlowBase):
    """`Flow` 读取模型。

    契约：
    - 输出字段：包含 `id`/`user_id`/`folder_id`/`tags`。
    - 用途：读取与列表展示。
    - 失败语义：字段校验失败抛异常。

    关键路径：输出包含 `id` 与标签信息。

    决策：`tags` 作为可选列表输出。
    问题：标签可能为空但仍需保持一致字段。
    方案：使用可选 `list[str]` 并保留描述。
    代价：调用方需处理 `None`。
    重评：当标签强制存在时改为必填。
    """
    id: UUID
    user_id: UUID | None = Field()
    folder_id: UUID | None = Field()
    tags: list[str] | None = Field(None, description="The tags of the flow")


class FlowHeader(BaseModel):
    """`Flow` 头部信息模型（不含完整图数据）。

    契约：
    - 输出字段：`id`/`name`/`folder_id` 等概要信息。
    - 用途：列表展示或轻量响应。
    - 失败语义：字段校验失败抛异常。

    关键路径：`data` 字段在校验器中按组件标记裁剪。

    决策：在 `is_component=False` 时清空 `data`。
    问题：组件与非组件的 `data` 字段语义不同。
    方案：在校验器中按 `is_component` 决定是否保留 `data`。
    代价：调用方需依赖该规则判断是否加载完整数据。
    重评：当组件逻辑调整时同步更新校验器。
    """

    id: UUID = Field(description="Unique identifier for the flow")
    name: str = Field(description="The name of the flow")
    folder_id: UUID | None = Field(
        None,
        description="The ID of the folder containing the flow. None if not associated with a folder",
    )
    is_component: bool | None = Field(None, description="Flag indicating whether the flow is a component")
    endpoint_name: str | None = Field(None, description="The name of the endpoint associated with this flow")
    description: str | None = Field(None, description="A description of the flow")
    data: dict | None = Field(None, description="The data of the component, if is_component is True")
    access_type: AccessTypeEnum | None = Field(None, description="The access type of the flow")
    tags: list[str] | None = Field(None, description="The tags of the flow")
    mcp_enabled: bool | None = Field(None, description="Flag indicating whether the flow is exposed in the MCP server")
    action_name: str | None = Field(None, description="The name of the action associated with the flow")
    action_description: str | None = Field(None, description="The description of the action associated with the flow")

    @field_validator("data", mode="before")
    @classmethod
    def validate_flow_header(cls, value: dict, info: ValidationInfo):
        """根据组件标记裁剪 `data` 字段。"""
        if not info.data["is_component"]:
            return None
        return value


class FlowUpdate(SQLModel):
    """`Flow` 更新输入模型。

    契约：
    - 输入字段：均为可选。
    - 输出：用于部分更新的结构化数据。
    - 失败语义：字段校验失败抛异常。

    关键路径：仅对传入字段进行校验与序列化。

    决策：更新模型允许部分字段更新。
    问题：流程更新通常是局部修改。
    方案：所有字段设为可选。
    代价：调用方需显式区分 `None` 与不更新。
    重评：当需要强制字段更新时改为必填字段。
    """
    name: str | None = None
    description: str | None = None
    data: dict | None = None
    folder_id: UUID | None = None
    endpoint_name: str | None = None
    mcp_enabled: bool | None = None
    locked: bool | None = None
    action_name: str | None = None
    action_description: str | None = None
    access_type: AccessTypeEnum | None = None
    fs_path: str | None = None

    @field_validator("endpoint_name")
    @classmethod
    def validate_endpoint_name(cls, v):
        """校验 `endpoint_name` 合法性。"""
        if v is not None:
            if not isinstance(v, str):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Endpoint name must be a string",
                )
            if not re.match(r"^[a-zA-Z0-9_-]+$", v):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Endpoint name must contain only letters, numbers, hyphens, and underscores",
                )
        return v
