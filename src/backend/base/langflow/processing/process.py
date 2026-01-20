"""
模块名称：Graph 执行与 Tweaks 处理

本模块提供图执行入口与流程 Tweaks 的应用逻辑。
主要功能包括：
- 执行 Graph 并返回运行输出
- 校验并应用节点 Tweaks
- 将 Tweaks 同步到 Graph 顶点参数

关键组件：
- `run_graph_internal` / `run_graph`
- `process_tweaks` / `process_tweaks_on_graph`

设计背景：API 与内部执行路径需要统一的输入处理与 Tweaks 行为。
注意事项：Tweaks 不允许覆盖 `code` 字段，防止代码注入。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from lfx.graph.vertex.base import Vertex
from lfx.log.logger import logger
from lfx.processing.utils import validate_and_repair_json
from pydantic import BaseModel

from langflow.schema.graph import InputValue, Tweaks
from langflow.schema.schema import INPUT_FIELD_NAME
from langflow.services.deps import get_settings_service

if TYPE_CHECKING:
    from lfx.events.event_manager import EventManager
    from lfx.graph.graph.base import Graph
    from lfx.graph.schema import RunOutputs
    from lfx.schema.schema import InputValueRequest


class Result(BaseModel):
    """执行结果包装结构。

    契约：`result` 为任意执行输出，`session_id` 为会话标识。
    失败语义：字段缺失由 Pydantic 校验抛错。
    """
    result: Any
    session_id: str


async def run_graph_internal(
    graph: Graph,
    flow_id: str,
    *,
    stream: bool = False,
    session_id: str | None = None,
    inputs: list[InputValueRequest] | None = None,
    outputs: list[str] | None = None,
    event_manager: EventManager | None = None,
) -> tuple[list[RunOutputs], str]:
    """执行 Graph 并返回输出与实际 session_id。

    契约：返回 `(run_outputs, effective_session_id)`。
    关键路径（三步）：
    1) 规范化输入列表与组件信息
    2) 设置 session_id 与运行参数
    3) 调用 `graph.arun` 获取结果
    副作用：设置 `graph.session_id` 并触发图执行。
    失败语义：由 `graph.arun` 抛出的异常向上传递。

    决策：`session_id` 缺失时回退为 `flow_id`
    问题：需要稳定的会话标识用于追踪
    方案：用 `flow_id` 作为默认会话
    代价：不同会话可能复用同一标识
    重评：当引入外部会话系统时改为强制传入
    """
    inputs = inputs or []
    effective_session_id = session_id or flow_id
    components = []
    inputs_list = []
    types = []
    for input_value_request in inputs:
        if input_value_request.input_value is None:
            await logger.awarning("InputValueRequest input_value cannot be None, defaulting to an empty string.")
            input_value_request.input_value = ""
        components.append(input_value_request.components or [])
        inputs_list.append({INPUT_FIELD_NAME: input_value_request.input_value})
        types.append(input_value_request.type)

    fallback_to_env_vars = get_settings_service().settings.fallback_to_env_var
    graph.session_id = effective_session_id
    run_outputs = await graph.arun(
        inputs=inputs_list,
        inputs_components=components,
        types=types,
        outputs=outputs or [],
        stream=stream,
        session_id=effective_session_id or "",
        fallback_to_env_vars=fallback_to_env_vars,
        event_manager=event_manager,
    )
    return run_outputs, effective_session_id


async def run_graph(
    graph: Graph,
    input_value: str,
    input_type: str,
    output_type: str,
    *,
    session_id: str | None = None,
    fallback_to_env_vars: bool = False,
    output_component: str | None = None,
    stream: bool = False,
) -> list[RunOutputs]:
    """执行 Graph 并返回输出列表。

    契约：`input_value`/`input_type` 必填，返回输出列表。
    关键路径（三步）：
    1) 组装单条输入为 `InputValue`
    2) 根据 `output_type` 选择输出节点
    3) 调用 `graph.arun` 获取结果
    副作用：触发图执行。
    失败语义：Graph 执行异常向上传递。

    决策：`output_component` 优先于 `output_type`
    问题：调用方可能指定明确输出节点
    方案：传入 `output_component` 时仅输出该节点
    代价：忽略 `output_type` 的过滤逻辑
    重评：若需同时支持二者可改为合并
    """
    inputs = [InputValue(components=[], input_value=input_value, type=input_type)]
    if output_component:
        outputs = [output_component]
    else:
        outputs = [
            vertex.id
            for vertex in graph.vertices
            if output_type == "debug"
            or (vertex.is_output and (output_type == "any" or output_type in vertex.id.lower()))
        ]
    components = []
    inputs_list = []
    types = []
    for input_value_request in inputs:
        if input_value_request.input_value is None:
            await logger.awarning("InputValueRequest input_value cannot be None, defaulting to an empty string.")
            input_value_request.input_value = ""
        components.append(input_value_request.components or [])
        inputs_list.append({INPUT_FIELD_NAME: input_value_request.input_value})
        types.append(input_value_request.type)
    return await graph.arun(
        inputs_list,
        inputs_components=components,
        types=types,
        outputs=outputs or [],
        stream=stream,
        session_id=session_id,
        fallback_to_env_vars=fallback_to_env_vars,
    )


def validate_input(
    graph_data: dict[str, Any], tweaks: Tweaks | dict[str, str | dict[str, Any]]
) -> list[dict[str, Any]]:
    """校验 graph_data 与 tweaks 结构并返回节点列表。

    契约：返回 `nodes` 列表，格式不符抛 `TypeError`。
    副作用：无。
    失败语义：`graph_data` 或 `tweaks` 非字典时抛异常。
    """
    if not isinstance(graph_data, dict) or not isinstance(tweaks, dict):
        msg = "graph_data and tweaks should be dictionaries"
        raise TypeError(msg)

    nodes = graph_data.get("data", {}).get("nodes") or graph_data.get("nodes")

    if not isinstance(nodes, list):
        msg = "graph_data should contain a list of nodes under 'data' key or directly under 'nodes' key"
        raise TypeError(msg)

    return nodes


def apply_tweaks(node: dict[str, Any], node_tweaks: dict[str, Any]) -> None:
    """将 tweaks 应用到单个节点模板。

    契约：仅修改 `template` 字段中的可匹配项。
    副作用：原地修改 `node` 字典。
    失败语义：模板结构异常时记录警告并返回。

    安全：禁止覆盖 `code` 字段
    问题：运行时注入代码存在安全风险
    方案：遇到 `code` 直接跳过并记录日志
    代价：无法通过 tweaks 修改代码
    重评：若引入安全沙箱可评估开放
    """
    template_data = node.get("data", {}).get("node", {}).get("template")

    if not isinstance(template_data, dict):
        logger.warning(f"Template data for node {node.get('id')} should be a dictionary")
        return

    for tweak_name, tweak_value in node_tweaks.items():
        if tweak_name not in template_data:
            continue
        if tweak_name == "code":
            logger.warning("Security: Code field cannot be overridden via tweaks.")
            continue
        if tweak_name in template_data:
            field_type = template_data[tweak_name].get("type", "")
            if field_type == "NestedDict":
                value = validate_and_repair_json(tweak_value)
                template_data[tweak_name]["value"] = value
            elif field_type == "mcp":
                # 注意：MCP 字段期望直接写入 dict 值
                template_data[tweak_name]["value"] = tweak_value
            elif isinstance(tweak_value, dict):
                for k, v in tweak_value.items():
                    k_ = "file_path" if field_type == "file" else k
                    template_data[tweak_name][k_] = v
            else:
                key = "file_path" if field_type == "file" else "value"
                template_data[tweak_name][key] = tweak_value


def apply_tweaks_on_vertex(vertex: Vertex, node_tweaks: dict[str, Any]) -> None:
    """将 tweaks 应用到 Graph 顶点参数。

    契约：仅更新 `vertex.params` 中已存在的键。
    副作用：原地修改 `vertex.params`。
    失败语义：不抛异常，非法字段被忽略。
    """
    for tweak_name, tweak_value in node_tweaks.items():
        if tweak_name and tweak_value and tweak_name in vertex.params:
            vertex.params[tweak_name] = tweak_value


def process_tweaks(
    graph_data: dict[str, Any], tweaks: Tweaks | dict[str, dict[str, Any]], *, stream: bool = False
) -> dict[str, Any]:
    """根据 tweaks 修改 graph_data 中的节点模板。

    契约：返回修改后的 `graph_data`。
    关键路径（三步）：
    1) 规范化 tweaks 并补充 `stream`
    2) 建立节点 id/显示名映射
    3) 应用节点级与全局 tweaks
    副作用：原地修改 `graph_data`。
    失败语义：输入结构不符合要求时抛 `TypeError`。

    决策：`stream` 未提供时注入全局值
    问题：部分调用方未显式设置 stream
    方案：缺失时写入默认 `stream` 参数
    代价：调用方无法区分“未设置”与“显式 False”
    重评：若需要三态逻辑可保留 None
    """
    tweaks_dict = cast("dict[str, Any]", tweaks.model_dump()) if not isinstance(tweaks, dict) else tweaks
    if "stream" not in tweaks_dict:
        tweaks_dict |= {"stream": stream}
    nodes = validate_input(graph_data, cast("dict[str, str | dict[str, Any]]", tweaks_dict))
    nodes_map = {node.get("id"): node for node in nodes}
    nodes_display_name_map = {node.get("data", {}).get("node", {}).get("display_name"): node for node in nodes}

    all_nodes_tweaks = {}
    for key, value in tweaks_dict.items():
        if isinstance(value, dict):
            if (node := nodes_map.get(key)) or (node := nodes_display_name_map.get(key)):
                apply_tweaks(node, value)
        else:
            all_nodes_tweaks[key] = value
    if all_nodes_tweaks:
        for node in nodes:
            apply_tweaks(node, all_nodes_tweaks)

    return graph_data


def process_tweaks_on_graph(graph: Graph, tweaks: dict[str, dict[str, Any]]):
    """将 tweaks 应用到 Graph 的顶点参数。

    契约：遍历所有顶点并按 `id` 应用对应 tweaks。
    副作用：原地修改顶点参数。
    失败语义：顶点结构异常时记录警告并跳过。
    """
    for vertex in graph.vertices:
        if isinstance(vertex, Vertex) and isinstance(vertex.id, str):
            node_id = vertex.id
            if node_tweaks := tweaks.get(node_id):
                apply_tweaks_on_vertex(vertex, node_tweaks)
        else:
            logger.warning("Each node should be a Vertex with an 'id' attribute of type str")

    return graph
