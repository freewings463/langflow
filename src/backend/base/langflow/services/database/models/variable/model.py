"""
模块名称：变量数据模型

本模块定义用户变量的数据库模型与读写模型。
主要功能包括：变量值存储、类型区分与读出时的敏感字段隐藏。

关键组件：`Variable` / `VariableCreate` / `VariableRead` / `VariableUpdate`
设计背景：统一变量字段结构并支持凭据类型隐藏。
使用场景：变量管理、凭据配置与运行时注入。
注意事项：`CREDENTIAL_TYPE` 的值在读取时会被隐藏。
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from pydantic import ValidationInfo, field_validator
from sqlmodel import JSON, Column, DateTime, Field, Relationship, SQLModel, func

from langflow.services.variable.constants import CREDENTIAL_TYPE

if TYPE_CHECKING:
    from langflow.services.database.models.user.model import User


def utc_now():
    """生成 `UTC` 时间戳。"""
    return datetime.now(timezone.utc)


class VariableBase(SQLModel):
    """变量基础字段模型。

    契约：
    - 字段：`name`/`value`/`default_fields`/`type`。
    - 用途：供创建与更新模型复用。
    - 失败语义：字段校验失败抛异常。

    关键路径：字段校验由 `SQLModel` 执行。

    决策：值字段存储为加密后的字符串。
    问题：变量可能包含敏感信息。
    方案：上层写入加密值并存储。
    代价：读取时需依赖解密流程。
    重评：当引入密钥管理服务时调整加密策略。
    """
    name: str = Field(description="Name of the variable")
    value: str = Field(description="Encrypted value of the variable")
    default_fields: list[str] | None = Field(sa_column=Column(JSON))
    type: str | None = Field(None, description="Type of the variable")


class Variable(VariableBase, table=True):  # type: ignore[call-arg]
    """变量数据库表模型。

    契约：
    - 主键：`id`。
    - 关键字段：`user_id`/`name`/`value`。
    - 关系：与 `User` 建立外键关系。
    - 失败语义：数据库约束冲突抛异常。

    决策：以 `user_id+name` 保证唯一性（由上层约束）。
    问题：同一用户不应出现重名变量。
    方案：在业务层确保名称唯一。
    代价：数据库层未显式建唯一约束。
    重评：当需要强一致时增加唯一索引。
    """
    id: UUID | None = Field(
        default_factory=uuid4,
        primary_key=True,
        description="Unique ID for the variable",
    )
    # 注意：名称在用户维度应保持唯一。
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=True),
        description="Creation time of the variable",
    )
    updated_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
        description="Last update time of the variable",
    )
    default_fields: list[str] | None = Field(sa_column=Column(JSON))
    # 注意：与 `user` 表建立外键关系。
    user_id: UUID = Field(description="User ID associated with this variable", foreign_key="user.id")
    user: "User" = Relationship(back_populates="variables")


class VariableCreate(VariableBase):
    """变量创建输入模型。

    契约：
    - 输入字段：继承 `VariableBase`，包含 `created_at`。
    - 输出：用于创建变量的结构化数据。
    - 失败语义：字段校验失败抛异常。

    决策：允许 `created_at` 由系统生成。
    问题：创建时间需要统一时区。
    方案：默认使用 `utc_now`。
    代价：无法记录外部来源时间。
    重评：当需要外部时间戳时允许输入覆盖。
    """
    created_at: datetime | None = Field(default_factory=utc_now, description="Creation time of the variable")


class VariableRead(SQLModel):
    """变量读取模型（凭据类型隐藏）。

    契约：
    - 输出字段：`id`/`name`/`type`/`value` 等。
    - 用途：变量列表与详情读取。
    - 失败语义：字段校验失败抛异常。

    关键路径：`value` 字段在校验阶段可能被置空。

    决策：当 `type` 为 `CREDENTIAL_TYPE` 时隐藏 `value`。
    问题：凭据不应在读取接口暴露明文。
    方案：在校验器中返回 `None`。
    代价：调用方无法获取明文值。
    重评：当支持权限分级时按权限返回部分信息。
    """
    id: UUID
    name: str | None = Field(None, description="Name of the variable")
    type: str | None = Field(None, description="Type of the variable")
    value: str | None = Field(None, description="Encrypted value of the variable")
    default_fields: list[str] | None = Field(None, description="Default fields for the variable")

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: str, info: ValidationInfo):
        """在凭据类型下隐藏变量值。"""
        if info.data.get("type") == CREDENTIAL_TYPE:
            return None
        return value


class VariableUpdate(SQLModel):
    """变量更新输入模型。

    契约：
    - 输入字段：均为可选，包含 `id`。
    - 用途：局部更新变量。
    - 失败语义：字段校验失败抛异常。

    关键路径：仅对传入字段进行校验。

    决策：要求 `id` 作为更新目标。
    问题：更新需要明确目标记录。
    方案：在更新模型中包含 `id`。
    代价：调用方必须传递 `id`。
    重评：当更新由路径参数提供时可移除 `id` 字段。
    """
    id: UUID  # 注意：更新需包含目标 `ID`。
    name: str | None = Field(None, description="Name of the variable")
    value: str | None = Field(None, description="Encrypted value of the variable")
    default_fields: list[str] | None = Field(None, description="Default fields for the variable")
    type: str | None = Field(None, description="Type of the variable")
