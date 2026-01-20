"""
模块名称：文件元数据模型

本模块定义文件元数据在数据库中的存储结构。
主要功能包括：记录文件路径、大小、提供方与时间戳。

关键组件：`File`
设计背景：统一文件元数据字段，支撑上传与检索。
使用场景：文件上传、存储管理与权限校验。
注意事项：`(name, user_id)` 组合需保持唯一。
"""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel, UniqueConstraint

from langflow.schema.serialize import UUIDstr


class File(SQLModel, table=True):  # type: ignore[call-arg]
    """文件元数据表模型。

    契约：
    - 主键：`id`。
    - 关键字段：`user_id`/`path`/`size`/`provider`。
    - 约束：`(name, user_id)` 唯一。
    - 失败语义：唯一约束冲突由数据库异常抛出。

    关键路径：由数据库唯一约束保证同名限制。

    决策：对 `name` 与 `user_id` 施加联合唯一约束。
    问题：同一用户不应出现重复命名文件记录。
    方案：通过数据库层唯一约束强制一致性。
    代价：重名文件需先删除或改名。
    重评：当允许同名多版本时改为增加版本字段。
    """
    id: UUIDstr = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="user.id")
    name: str = Field(nullable=False)
    path: str = Field(nullable=False)
    size: int = Field(nullable=False)
    provider: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint("name", "user_id"),)
