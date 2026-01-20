"""
模块名称：文件夹数据模型

本模块定义文件夹的数据库模型与读写模型。
主要功能包括：文件夹层级关系、用户归属与权限设置存储。

关键组件：`Folder` / `FolderCreate` / `FolderRead`
设计背景：统一文件夹结构与授权配置的持久化模型。
使用场景：文件夹创建、更新与列表展示。
注意事项：同一用户下 `name` 需唯一。
"""

from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import Text, UniqueConstraint
from sqlmodel import JSON, Column, Field, Relationship, SQLModel

from langflow.services.database.models.flow.model import Flow, FlowRead
from langflow.services.database.models.user.model import User


class FolderBase(SQLModel):
    """文件夹基础字段模型。

    契约：
    - 字段：`name`/`description`/`auth_settings`。
    - 用途：供创建与读取模型复用。
    - 失败语义：字段校验失败抛异常。

    关键路径：字段序列化由 `SQLModel` 处理。

    决策：将授权配置存储为 `JSON`。
    问题：不同文件夹可能有不同鉴权配置。
    方案：使用 `auth_settings` 字段保存配置。
    代价：缺乏结构化约束。
    重评：当配置稳定后可拆分为结构化字段。
    """
    name: str = Field(index=True)
    description: str | None = Field(default=None, sa_column=Column(Text))
    auth_settings: dict | None = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
        description="Authentication settings for the folder/project",
    )


class Folder(FolderBase, table=True):  # type: ignore[call-arg]
    """文件夹数据库表模型。

    契约：
    - 主键：`id`。
    - 关键字段：`user_id`/`parent_id`/`name`。
    - 关系：自引用父子关系与 `Flow` 关联。
    - 失败语义：唯一约束冲突时抛数据库异常。

    关键路径：通过 `parent_id` 构建树形结构。

    决策：通过 `parent_id` 表达层级结构。
    问题：需要支持多级文件夹树。
    方案：自引用外键并建立 `children` 关系。
    代价：层级查询复杂度提升。
    重评：当层级过深影响性能时改为路径枚举存储。
    """
    id: UUID | None = Field(default_factory=uuid4, primary_key=True)
    parent_id: UUID | None = Field(default=None, foreign_key="folder.id")

    parent: Optional["Folder"] = Relationship(
        back_populates="children",
        sa_relationship_kwargs={"remote_side": "Folder.id"},
    )
    children: list["Folder"] = Relationship(back_populates="parent")
    user_id: UUID | None = Field(default=None, foreign_key="user.id")
    user: User = Relationship(back_populates="folders")
    flows: list[Flow] = Relationship(
        back_populates="folder", sa_relationship_kwargs={"cascade": "all, delete, delete-orphan"}
    )

    __table_args__ = (UniqueConstraint("user_id", "name", name="unique_folder_name"),)


class FolderCreate(FolderBase):
    """文件夹创建输入模型。

    契约：
    - 输入字段：`components_list`/`flows_list` 可选。
    - 输出：用于创建文件夹的结构化数据。
    - 失败语义：字段校验失败抛异常。

    关键路径：可选列表字段由调用方提供。

    决策：允许创建时绑定组件与流程列表。
    问题：创建后常需批量绑定资源。
    方案：提供可选列表字段。
    代价：创建逻辑需要额外处理绑定关系。
    重评：当绑定改为异步操作时移除该字段。
    """
    components_list: list[UUID] | None = None
    flows_list: list[UUID] | None = None


class FolderRead(FolderBase):
    """文件夹读取模型。

    契约：
    - 输出字段：包含 `id` 与 `parent_id`。
    - 用途：列表与详情读取。
    - 失败语义：字段校验失败抛异常。

    关键路径：读取时包含 `parent_id` 用于构建目录树。

    决策：保留 `parent_id` 以支持树形展示。
    问题：需要快速构建目录树。
    方案：在读取模型中包含 `parent_id`。
    代价：前端需额外拼装树形结构。
    重评：当后端直接提供树结构时可移除。
    """
    id: UUID
    parent_id: UUID | None = Field()


class FolderReadWithFlows(FolderBase):
    """包含流程列表的文件夹读取模型。

    契约：
    - 输出字段：`flows` 为 `FlowRead` 列表。
    - 用途：一次性读取文件夹与流程明细。
    - 失败语义：字段校验失败抛异常。

    关键路径：`flows` 列表由查询层填充。

    决策：默认返回空列表而非 `None`。
    问题：调用方希望统一遍历逻辑。
    方案：使用 `default=[]`。
    代价：可变默认需注意序列化拷贝。
    重评：当需要区分“未加载”时改为 `None`。
    """
    id: UUID
    parent_id: UUID | None = Field()
    flows: list[FlowRead] = Field(default=[])


class FolderUpdate(SQLModel):
    """文件夹更新输入模型。

    契约：
    - 输入字段：均为可选。
    - 输出：用于局部更新的结构化数据。
    - 失败语义：字段校验失败抛异常。

    关键路径：仅对传入字段进行校验与序列化。

    决策：允许同时更新绑定组件与流程列表。
    问题：文件夹更新可能涉及资源重新归属。
    方案：提供 `components`/`flows` 列表字段。
    代价：更新逻辑需处理增删差异。
    重评：当改为异步批量更新时移除此功能。
    """
    name: str | None = None
    description: str | None = None
    parent_id: UUID | None = None
    components: list[UUID] = Field(default_factory=list)
    flows: list[UUID] = Field(default_factory=list)
    auth_settings: dict | None = None
