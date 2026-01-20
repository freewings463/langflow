"""Flow 辅助函数。

本模块提供图流程输入构建与运行的轻量实现，适用于无数据库场景。
注意事项：在 lfx 环境中多为占位实现，会返回空列表或抛出未实现错误。
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, Field, create_model

from lfx.log.logger import logger
from lfx.schema.schema import INPUT_FIELD_NAME

if TYPE_CHECKING:
    from lfx.graph.graph.base import Graph
    from lfx.graph.schema import RunOutputs
    from lfx.graph.vertex.base import Vertex
    from lfx.schema.data import Data


def get_flow_inputs(graph: Graph) -> list[Vertex]:
    """获取图中的输入节点列表。

    关键路径（三步）：
    1) 遍历图中所有顶点；
    2) 筛选 `is_input` 为 True 的节点；
    3) 返回输入节点列表。
    """
    return [vertex for vertex in graph.vertices if vertex.is_input]


def build_schema_from_inputs(name: str, inputs: list[Vertex]) -> type[BaseModel]:
    """根据输入节点生成 Pydantic Schema。

    关键路径（三步）：
    1) 归一化输入显示名为字段名；
    2) 采集描述并设置默认值；
    3) 动态创建并返回模型。
    """
    fields = {}
    for input_ in inputs:
        field_name = input_.display_name.lower().replace(" ", "_")
        description = input_.description
        fields[field_name] = (str, Field(default="", description=description))
    return create_model(name, **fields)


def get_arg_names(inputs: list[Vertex]) -> list[dict[str, str]]:
    """返回组件名与参数名映射列表。

    关键路径（三步）：
    1) 遍历输入节点；
    2) 生成组件名与参数名；
    3) 返回映射列表。
    """
    return [
        {"component_name": input_.display_name, "arg_name": input_.display_name.lower().replace(" ", "_")}
        for input_ in inputs
    ]


async def list_flows(*, user_id: str | None = None) -> list[Data]:
    """列出用户可用的 flows（lfx 占位实现）。

    关键路径（三步）：
    1) 校验用户会话；
    2) 记录占位警告；
    3) 返回空列表。
    """
    if not user_id:
        msg = "Session is invalid"
        raise ValueError(msg)

    # 注意：lfx 默认无数据库，实现为占位
    logger.warning("list_flows called but lfx doesn't have database backend by default")
    return []


async def list_flows_by_flow_folder(
    *,
    user_id: str | None = None,
    flow_id: str | None = None,
    order_params: dict | None = {"column": "updated_at", "direction": "desc"},  # noqa: B006, ARG001
) -> list[Data]:
    """列出与指定 flow 同目录的 flows（lfx 占位实现）。

    关键路径（三步）：
    1) 校验用户与 flow ID；
    2) 记录占位警告；
    3) 返回空列表。
    """
    if not user_id:
        msg = "Session is invalid"
        raise ValueError(msg)
    if not flow_id:
        msg = "Flow ID is required"
        raise ValueError(msg)

    # 注意：lfx 默认无数据库，实现为占位
    logger.warning("list_flows_by_flow_folder called but lfx doesn't have database backend by default")
    return []


async def list_flows_by_folder_id(
    *,
    user_id: str | None = None,
    folder_id: str | None = None,
) -> list[Data]:
    """列出指定目录下的 flows（lfx 占位实现）。

    关键路径（三步）：
    1) 校验用户与目录 ID；
    2) 记录占位警告；
    3) 返回空列表。
    """
    if not user_id:
        msg = "Session is invalid"
        raise ValueError(msg)
    if not folder_id:
        msg = "Folder ID is required"
        raise ValueError(msg)

    # 注意：lfx 默认无数据库，实现为占位
    logger.warning("list_flows_by_folder_id called but lfx doesn't have database backend by default")
    return []


async def get_flow_by_id_or_name(
    user_id: str,
    flow_id: str | None = None,
    flow_name: str | None = None,
) -> Data | None:
    """按 ID 或名称获取 flow（lfx 占位实现）。

    关键路径（三步）：
    1) 校验用户与查询条件；
    2) 记录占位警告；
    3) 返回 None。
    """
    if not user_id:
        msg = "Session is invalid"
        raise ValueError(msg)
    if not (flow_id or flow_name):
        msg = "Flow ID or Flow Name is required"
        raise ValueError(msg)

    # 注意：lfx 默认无数据库，实现为占位
    logger.warning("get_flow_by_id_or_name called but lfx doesn't have database backend by default")
    return None


async def load_flow(
    user_id: str,  # noqa: ARG001
    flow_id: str | None = None,
    flow_name: str | None = None,
    tweaks: dict | None = None,  # noqa: ARG001
) -> Graph:
    """加载 flow（lfx 占位实现，直接抛错）。

    关键路径（三步）：
    1) 校验 flow 标识；
    2) 构造未实现错误；
    3) 抛出异常。
    """
    if not flow_id and not flow_name:
        msg = "Flow ID or Flow Name is required"
        raise ValueError(msg)

    # 注意：lfx 默认无数据库，实现为占位
    msg = f"load_flow not implemented in lfx - cannot load flow {flow_id or flow_name}"
    raise NotImplementedError(msg)


async def run_flow(
    inputs: dict | list[dict] | None = None,
    tweaks: dict | None = None,  # noqa: ARG001
    flow_id: str | None = None,  # noqa: ARG001
    flow_name: str | None = None,  # noqa: ARG001
    output_type: str | None = "chat",
    user_id: str | None = None,
    run_id: str | None = None,
    session_id: str | None = None,
    graph: Graph | None = None,
) -> list[RunOutputs]:
    """运行 flow（要求提供 graph）。

    关键路径（三步）：
    1) 校验用户与图对象；
    2) 组装输入与输出节点；
    3) 调用 `graph.arun` 返回结果。
    """
    if user_id is None:
        msg = "Session is invalid"
        raise ValueError(msg)

    if graph is None:
        # 注意：lfx 无数据库，必须显式传入 graph
        msg = "run_flow requires a graph parameter in lfx"
        raise ValueError(msg)

    if run_id:
        graph.set_run_id(UUID(run_id))
    if session_id:
        graph.session_id = session_id
    if user_id:
        graph.user_id = user_id

    if inputs is None:
        inputs = []
    if isinstance(inputs, dict):
        inputs = [inputs]

    inputs_list = []
    inputs_components = []
    types = []

    for input_dict in inputs:
        inputs_list.append({INPUT_FIELD_NAME: input_dict.get("input_value", "")})
        inputs_components.append(input_dict.get("components", []))
        types.append(input_dict.get("type", "chat"))

    outputs = [
        vertex.id
        for vertex in graph.vertices
        if output_type == "debug"
        or (vertex.is_output and (output_type == "any" or (output_type and output_type in str(vertex.id).lower())))
    ]

    # 注意：lfx 无 settings service，默认 False
    fallback_to_env_vars = False

    return await graph.arun(
        inputs_list,
        outputs=outputs,
        inputs_components=inputs_components,
        types=types,
        fallback_to_env_vars=fallback_to_env_vars,
    )
