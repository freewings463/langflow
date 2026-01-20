"""
模块名称：用户数据模型

本模块定义用户表结构与读写模型。
主要功能包括：用户基础信息、权限字段与关联关系建模。

关键组件：`User` / `UserCreate` / `UserRead` / `UserUpdate`
设计背景：统一用户字段与权限标记，便于鉴权与审计。
使用场景：用户注册、登录与管理接口。
注意事项：`username` 唯一；`optins` 用于记录用户偏好。
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy import JSON, Column
from sqlmodel import Field, Relationship, SQLModel

from langflow.schema.serialize import UUIDstr

if TYPE_CHECKING:
    from langflow.services.database.models.api_key.model import ApiKey
    from langflow.services.database.models.flow.model import Flow
    from langflow.services.database.models.folder.model import Folder
    from langflow.services.database.models.variable.model import Variable


class UserOptin(BaseModel):
    """用户自愿动作记录模型。

    契约：
    - 字段：`github_starred`/`dialog_dismissed`/`discord_clicked`。
    - 用途：记录用户自愿行为。
    - 失败语义：字段校验失败抛异常。

    关键路径：字段以布尔值表示用户动作状态。

    决策：使用布尔字段记录行为状态。
    问题：需要轻量记录用户动作而不引入复杂事件表。
    方案：在用户表中以 `JSON` 记录布尔值。
    代价：可追溯性较弱。
    重评：当需要完整行为审计时改为事件表。
    """
    github_starred: bool = Field(default=False)
    dialog_dismissed: bool = Field(default=False)
    discord_clicked: bool = Field(default=False)
    # 注意：需要新增 opt-in 字段时在此扩展。


class User(SQLModel, table=True):  # type: ignore[call-arg]
    """用户数据库表模型。

    契约：
    - 主键：`id`。
    - 关键字段：`username`/`password`/`is_superuser`。
    - 关系：与 `ApiKey`/`Flow`/`Folder`/`Variable` 关联。
    - 失败语义：唯一约束冲突由数据库异常抛出。

    决策：`optins` 使用 `JSON` 存储。
    问题：用户偏好字段可变且不固定。
    方案：使用 `JSON` 字段存储键值。
    代价：缺乏结构化约束。
    重评：当偏好字段稳定后拆为独立列或表。
    """
    id: UUIDstr = Field(default_factory=uuid4, primary_key=True, unique=True)
    username: str = Field(index=True, unique=True)
    password: str = Field()
    profile_image: str | None = Field(default=None, nullable=True)
    is_active: bool = Field(default=False)
    is_superuser: bool = Field(default=False)
    create_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_login_at: datetime | None = Field(default=None, nullable=True)
    api_keys: list["ApiKey"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"cascade": "delete"},
    )
    store_api_key: str | None = Field(default=None, nullable=True)
    flows: list["Flow"] = Relationship(back_populates="user")
    variables: list["Variable"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"cascade": "delete"},
    )
    folders: list["Folder"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"cascade": "delete"},
    )
    optins: dict[str, Any] | None = Field(
        sa_column=Column(JSON, default=lambda: UserOptin().model_dump(), nullable=True)
    )


class UserCreate(SQLModel):
    """用户创建输入模型。

    契约：
    - 输入字段：`username`/`password`/`optins`。
    - 输出：用于创建用户的数据结构。
    - 失败语义：字段校验失败抛异常。

    关键路径：字段校验由 `Pydantic` 执行。

    决策：提供默认 `optins` 值。
    问题：新用户需要明确默认偏好设置。
    方案：在模型中设置默认值。
    代价：变更默认值需同步更新模型。
    重评：当偏好设置迁移到配置中心时移除默认值。
    """
    username: str = Field()
    password: str = Field()
    optins: dict[str, Any] | None = Field(
        default={"github_starred": False, "dialog_dismissed": False, "discord_clicked": False}
    )


class UserRead(SQLModel):
    """用户读取模型。

    契约：
    - 输出字段：包含权限与时间戳信息。
    - 用途：列表与详情读取。
    - 失败语义：字段校验失败抛异常。

    关键路径：只读字段由模型校验输出。

    决策：读取模型包含 `store_api_key` 字段。
    问题：部分调用方需要显示是否配置存储密钥。
    方案：暴露 `store_api_key` 但保持可空。
    代价：敏感字段需谨慎对外展示。
    重评：当安全策略调整时移除该字段。
    """
    id: UUID = Field(default_factory=uuid4)
    username: str = Field()
    profile_image: str | None = Field()
    store_api_key: str | None = Field(nullable=True)
    is_active: bool = Field()
    is_superuser: bool = Field()
    create_at: datetime = Field()
    updated_at: datetime = Field()
    last_login_at: datetime | None = Field(nullable=True)
    optins: dict[str, Any] | None = Field(default=None)


class UserUpdate(SQLModel):
    """用户更新输入模型。

    契约：
    - 输入字段：均为可选。
    - 输出：用于局部更新的数据结构。
    - 失败语义：字段校验失败抛异常。

    关键路径：仅对传入字段进行校验与序列化。

    决策：允许同时更新权限与资料字段。
    问题：管理端需要统一更新入口。
    方案：将字段全部设为可选。
    代价：调用方需区分 `None` 与不更新。
    重评：当权限更新需单独审批时拆分模型。
    """
    username: str | None = None
    profile_image: str | None = None
    password: str | None = None
    is_active: bool | None = None
    is_superuser: bool | None = None
    last_login_at: datetime | None = None
    optins: dict[str, Any] | None = None
