"""模块名称：图运行时结构体定义

本模块定义图序列化与构建阶段用到的 TypedDict/NamedTuple 协议。
使用场景：Graph 序列化、构建结果传递与日志回调签名。
主要功能包括：
- Graph 数据结构与导出格式
- 顶点构建结果结构
- 运行输出配置结构
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple, Protocol

from typing_extensions import NotRequired, TypedDict

from lfx.graph.edge.schema import EdgeData
from lfx.graph.vertex.schema import NodeData

if TYPE_CHECKING:
    from lfx.graph.schema import ResultData
    from lfx.graph.vertex.base import Vertex
    from lfx.schema.log import LoggableType


class ViewPort(TypedDict):
    """画布视口信息。"""
    x: float
    y: float
    zoom: float


class GraphData(TypedDict):
    """图数据载体（节点/边/视口）。"""
    nodes: list[NodeData]
    edges: list[EdgeData]
    viewport: NotRequired[ViewPort]


class GraphDump(TypedDict, total=False):
    """图导出结构。"""
    data: GraphData
    is_component: bool
    name: str
    description: str
    endpoint_name: str


class VertexBuildResult(NamedTuple):
    """顶点构建结果。"""
    result_dict: ResultData
    params: str
    valid: bool
    artifacts: dict
    vertex: Vertex


class OutputConfigDict(TypedDict):
    """输出配置。"""
    cache: bool


class StartConfigDict(TypedDict):
    """启动配置。"""
    output: OutputConfigDict


class LogCallbackFunction(Protocol):
    """日志回调签名协议。"""
    def __call__(self, event_name: str, log: LoggableType) -> None: ...
