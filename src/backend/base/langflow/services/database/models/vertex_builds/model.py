"""
模块名称：节点构建记录模型

本模块定义节点构建记录的持久化模型与序列化规则。
主要功能包括：记录构建数据、制品与参数，并限制字段大小。

关键组件：`VertexBuildTable` / `VertexBuildMapModel`
设计背景：统一节点构建过程的日志与结果存储结构。
使用场景：流程执行调试与历史回放。
注意事项：`data`/`artifacts`/`params` 会进行长度限制序列化。
"""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from pydantic import BaseModel, field_serializer, field_validator
from sqlalchemy import Text
from sqlmodel import JSON, Column, Field, SQLModel

from langflow.serialization.serialization import get_max_items_length, get_max_text_length, serialize


class VertexBuildBase(SQLModel):
    """节点构建基础字段模型。

    契约：
    - 字段：`timestamp`/`id`/`data`/`artifacts`/`params` 等。
    - 用途：供表模型复用。
    - 失败语义：字段校验失败抛异常。

    关键路径：序列化器负责限制字段大小。

    决策：使用序列化函数限制字段长度与项数。
    问题：构建数据可能体积过大。
    方案：在序列化阶段统一裁剪。
    代价：可能丢失部分细节。
    重评：当支持外部存储时改为引用存储。
    """
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    id: str = Field(nullable=False)
    data: dict | None = Field(default=None, sa_column=Column(JSON))
    artifacts: dict | None = Field(default=None, sa_column=Column(JSON))
    params: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    valid: bool = Field(nullable=False)
    flow_id: UUID = Field()

    # 注意：允许 `JSON` 列类型的任意对象。
    class Config:
        arbitrary_types_allowed = True

    @field_validator("flow_id", mode="before")
    @classmethod
    def validate_flow_id(cls, value):
        """将 `flow_id` 字符串转为 `UUID`。"""
        if value is None:
            return value
        if isinstance(value, str):
            value = UUID(value)
        return value

    @field_serializer("timestamp")
    @classmethod
    def serialize_timestamp(cls, value):
        """为 `timestamp` 补齐时区信息。"""
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value

    @field_serializer("data")
    def serialize_data(self, data) -> dict:
        """序列化 `data` 并限制长度与项数。"""
        return serialize(data, max_length=get_max_text_length(), max_items=get_max_items_length())

    @field_serializer("artifacts")
    def serialize_artifacts(self, data) -> dict:
        """序列化 `artifacts` 并限制长度与项数。"""
        return serialize(data, max_length=get_max_text_length(), max_items=get_max_items_length())

    @field_serializer("params")
    def serialize_params(self, data) -> str:
        """序列化 `params` 并限制长度与项数。"""
        return serialize(data, max_length=get_max_text_length(), max_items=get_max_items_length())


class VertexBuildTable(VertexBuildBase, table=True):  # type: ignore[call-arg]
    """节点构建记录表模型。

    契约：
    - 主键：`build_id`。
    - 关键字段：`id`（节点标识）与 `flow_id`。
    - 用途：持久化节点构建记录。
    - 失败语义：约束冲突由数据库异常抛出。

    关键路径：序列化规则继承自 `VertexBuildBase`。

    决策：使用独立 `build_id` 作为主键。
    问题：节点 `id` 可能重复产生多次构建记录。
    方案：将每次构建视为独立记录。
    代价：同一节点存在多条记录。
    重评：当仅需最新记录时可改为覆盖更新。
    """
    __tablename__ = "vertex_build"
    build_id: UUID | None = Field(default_factory=uuid4, primary_key=True)


class VertexBuildMapModel(BaseModel):
    """节点构建记录按 `id` 分组的响应模型。

    契约：
    - 输出字段：`vertex_builds` 映射。
    - 用途：按节点聚合构建记录。
    - 失败语义：字段校验失败抛异常。

    关键路径：`vertex_builds` 由分组函数构建。

    决策：输出为 `id -> list` 的字典结构。
    问题：调用方需要按节点快速访问构建历史。
    方案：在模型中直接提供分组结果。
    代价：构建分组需要额外遍历。
    重评：当只需扁平列表时改为列表响应。
    """
    vertex_builds: dict[str, list[VertexBuildTable]]

    @classmethod
    def from_list_of_dicts(cls, vertex_build_dicts: list[VertexBuildTable]):
        """将构建记录列表转换为按 `id` 分组的结构。

        契约：
        - 输入：`VertexBuildTable` 列表。
        - 输出：`VertexBuildMapModel` 实例。
        - 副作用：无。
        - 失败语义：输入类型不匹配时抛异常。

        关键路径：遍历列表并按 `id` 分桶。

        决策：按 `id` 分桶聚合。
        问题：同一节点可能存在多条构建记录。
        方案：以字典方式聚合列表。
        代价：需要额外内存存放分组结构。
        重评：当需要按时间窗口聚合时改为分层分组。
        """
        vertex_build_map: dict[str, list[VertexBuildTable]] = {}
        for vertex_build in vertex_build_dicts:
            if vertex_build.id not in vertex_build_map:
                vertex_build_map[vertex_build.id] = []
            vertex_build_map[vertex_build.id].append(vertex_build)
        return cls(vertex_builds=vertex_build_map)
