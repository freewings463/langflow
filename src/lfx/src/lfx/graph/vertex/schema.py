"""
模块名称：Vertex 节点结构定义

模块目的：定义图节点数据结构与枚举类型。
使用场景：图的序列化/反序列化与前后端数据交换。
主要功能包括：
- 节点类型枚举 `NodeTypeEnum`
- 节点位置 `Position`
- 节点数据结构 `NodeData`

设计背景：使用 TypedDict 保持与前端数据结构一致。
注意：字段名与前端协议耦合，修改需同步前端。
"""

from enum import Enum

from typing_extensions import NotRequired, TypedDict


class NodeTypeEnum(str, Enum):
    """节点类型枚举。

    契约：与前端节点类型字符串保持一致。
    """
    NoteNode = "noteNode"
    GenericNode = "genericNode"


class Position(TypedDict):
    """节点坐标结构。

    契约：包含 `x`/`y` 两个浮点坐标。
    """
    x: float
    y: float


class NodeData(TypedDict):
    """节点数据结构定义。

    契约：包含必要的 `id`/`data` 字段，并可选位置与尺寸信息。
    注意：字段名遵循前端协议，保持兼容性。
    """
    id: str
    data: dict
    dragging: NotRequired[bool]
    height: NotRequired[int]
    width: NotRequired[int]
    position: NotRequired[Position]
    positionAbsolute: NotRequired[Position]
    selected: NotRequired[bool]
    parent_node_id: NotRequired[str]
    type: NotRequired[NodeTypeEnum]
