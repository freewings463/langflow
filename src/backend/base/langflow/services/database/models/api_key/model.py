"""
模块名称：`ApiKey` 数据模型

本模块定义 `API Key` 的数据库模型与序列化模型。
主要功能包括：持久化密钥、记录使用信息并支持遮罩输出。

关键组件：`ApiKey` / `ApiKeyCreate` / `ApiKeyRead` / `UnmaskedApiKeyRead`
设计背景：统一 `API Key` 字段结构与读写模型。
使用场景：创建、读取与鉴权校验 `API Key`。
注意事项：`ApiKeyRead` 会对密钥进行遮罩处理。
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import field_validator
from sqlmodel import Column, DateTime, Field, Relationship, SQLModel, func

from langflow.schema.serialize import UUIDstr

if TYPE_CHECKING:
    from langflow.services.database.models.user.model import User


def utc_now():
    """生成 `UTC` 时间戳。

    契约：
    - 输入：无。
    - 输出：`datetime`（`timezone.utc`）。
    - 副作用：无。
    - 失败语义：无显式抛错。
    """
    return datetime.now(timezone.utc)


class ApiKeyBase(SQLModel):
    """`API Key` 基础字段模型。

    契约：
    - 字段：`name`/`last_used_at`/`total_uses`/`is_active`。
    - 用途：在创建与读取模型间复用公共字段。
    - 失败语义：字段校验失败时由 `SQLModel` 抛出。

    关键路径：字段由 `SQLModel` 统一校验与序列化。

    决策：提取公共字段减少重复定义。
    问题：创建/读取模型共享字段较多。
    方案：使用基类继承复用字段。
    代价：字段变更需关注所有子类影响。
    重评：当字段分化明显时拆分为多个基类。
    """
    name: str | None = Field(index=True, nullable=True, default=None)
    last_used_at: datetime | None = Field(default=None, nullable=True)
    total_uses: int = Field(default=0)
    is_active: bool = Field(default=True)


class ApiKey(ApiKeyBase, table=True):  # type: ignore[call-arg]
    """`API Key` 数据库表模型。

    契约：
    - 主键：`id`。
    - 关键字段：`api_key`、`user_id`、`created_at`。
    - 副作用：与 `User` 建立外键关系。
    - 失败语义：持久化失败由数据库异常抛出。

    关键路径：主键与唯一约束由数据库层保证。

    决策：使用 `uuid4` 作为主键与唯一标识。
    问题：需要全局唯一且难以预测的标识符。
    方案：采用 `UUID` 作为主键。
    代价：索引体积较大。
    重评：当需要有序主键时改为 `ULID` 或序列号。
    """
    id: UUIDstr = Field(default_factory=uuid4, primary_key=True, unique=True)
    created_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    )
    api_key: str = Field(index=True, unique=True)
    # 注意：与 `User` 建立关系，用户删除时级联删除密钥。
    user_id: UUIDstr = Field(index=True, foreign_key="user.id")
    user: "User" = Relationship(
        back_populates="api_keys",
    )


class ApiKeyCreate(ApiKeyBase):
    """`API Key` 创建输入模型。

    契约：
    - 输入字段：`api_key`/`user_id`/`created_at`。
    - 输出：用于创建记录的结构化数据。
    - 失败语义：字段校验失败抛异常。

    关键路径：缺失 `created_at` 时由校验器补齐。

    决策：允许 `created_at` 由客户端或系统生成。
    问题：批量导入或迁移需要自定义创建时间。
    方案：提供 `created_at` 可选字段并在缺失时补默认值。
    代价：可能引入非单调时间。
    重评：当需要严格服务端时间时移除外部输入。
    """
    api_key: str | None = None
    user_id: UUIDstr | None = None
    created_at: datetime | None = Field(default_factory=utc_now)

    @field_validator("created_at", mode="before")
    @classmethod
    def set_created_at(cls, v):
        """为缺失时间补充 `UTC` 时间戳。"""
        return v or utc_now()


class UnmaskedApiKeyRead(ApiKeyBase):
    """`API Key` 明文读取模型。

    契约：
    - 输出字段：包含明文 `api_key`。
    - 使用场景：仅用于创建时返回一次明文。
    - 失败语义：字段校验失败抛异常。

    关键路径：仅在创建流程中返回该模型。

    决策：单独模型承载明文密钥输出。
    问题：普通读取需遮罩，但创建后需返回一次明文。
    方案：区分明文与遮罩的读取模型。
    代价：调用方需区分使用场景。
    重评：当安全策略更严格时改为仅返回部分明文。
    """
    id: UUIDstr
    api_key: str = Field()
    user_id: UUIDstr = Field()


class ApiKeyRead(ApiKeyBase):
    """`API Key` 遮罩读取模型。

    契约：
    - 输出字段：`api_key` 经遮罩处理。
    - 使用场景：列表或详情读取。
    - 失败语义：字段校验失败抛异常。

    关键路径：通过 `field_validator` 对 `api_key` 做遮罩。

    决策：遮罩仅保留前 8 位。
    问题：需要在不泄露密钥的情况下展示识别信息。
    方案：保留前缀并以 `*` 替代剩余字符。
    代价：当密钥前缀过短时识别度下降。
    重评：当安全策略变化时调整保留长度。
    """
    id: UUIDstr
    api_key: str = Field(schema_extra={"validate_default": True})
    user_id: UUIDstr = Field()
    created_at: datetime = Field()

    @field_validator("api_key")
    @classmethod
    def mask_api_key(cls, v) -> str:
        """将密钥遮罩为前缀 + `*`。"""
        return f"{v[:8]}{'*' * (len(v) - 8)}"
