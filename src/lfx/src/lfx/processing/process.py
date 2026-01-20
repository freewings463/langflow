"""
模块名称：lfx.processing.process

本模块提供图运行与 tweaks 应用的核心流程，主要用于在请求期执行图并动态调整节点参数。主要功能包括：
- 功能1：运行图并返回输出（`run_graph`/`run_graph_internal`）
- 功能2：校验与应用 tweaks（`process_tweaks`/`process_tweaks_on_graph`）
- 功能3：在解析输入时修复非标准 JSON（`validate_and_repair_json`）

关键组件：
- `run_graph_internal`：内部运行入口，支持输出选择与事件管理
- `run_graph`：简化运行入口
- `process_tweaks`：对图数据结构应用 tweaks

设计背景：在运行期统一处理输入修复与参数覆写，避免调用方实现细节不一致。
注意事项：tweaks 需与节点 `template` 字段结构匹配；无效字段会被跳过。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

from json_repair import repair_json
from pydantic import BaseModel

from lfx.graph.vertex.base import Vertex
from lfx.log.logger import logger
from lfx.schema.graph import InputValue, Tweaks
from lfx.schema.schema import INPUT_FIELD_NAME, InputValueRequest
from lfx.services.deps import get_settings_service

if TYPE_CHECKING:
    from lfx.events.event_manager import EventManager
    from lfx.graph.graph.base import Graph
    from lfx.graph.schema import RunOutputs


def validate_and_repair_json(json_str: str | dict) -> dict[str, Any] | str:
    """验证并尽量修复 JSON 字符串。

    契约：输入字符串或字典；若可修复则返回 dict，否则返回原字符串。
    关键路径（三步）：1) 非字符串直接返回 2) `repair_json` 修复 3) `json.loads` 解析。
    异常流：解析失败返回原字符串；不会抛异常给调用方。
    排障入口：检查传入字段是否为 `NestedDict` 类型。
    """
    if not isinstance(json_str, str):
        return json_str
    try:
        repaired = repair_json(json_str)
        return json.loads(repaired)
    except (json.JSONDecodeError, ImportError):
        return json_str


class Result(BaseModel):
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
    """执行图并返回输出与实际 session_id。

    契约：输入 `graph`/`inputs`/`outputs` 等配置，返回 `(run_outputs, effective_session_id)`。
    关键路径（三步）：1) 规范化输入 2) 读取配置开关 3) 调用 `graph.arun`。
    异常流：依赖 `graph.arun` 抛出；本函数不吞异常。
    性能瓶颈：取决于图内组件运行时；本函数主要为参数组织。
    排障入口：日志关键字 `InputValueRequest input_value cannot be None`。
    """
    inputs = inputs or []
    effective_session_id = session_id or flow_id
    components = []
    inputs_list = []
    types = []
    for input_value_request in inputs:
        if input_value_request.input_value is None:
            logger.warning("InputValueRequest input_value cannot be None, defaulting to an empty string.")
            input_value_request.input_value = ""
        components.append(input_value_request.components or [])
        inputs_list.append({INPUT_FIELD_NAME: input_value_request.input_value})
        types.append(input_value_request.type)

    try:
        fallback_to_env_vars = get_settings_service().settings.fallback_to_env_var
    except (AttributeError, TypeError):
        fallback_to_env_vars = False

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
    """简化运行入口：根据输入与输出类型执行图。

    契约：输入 `input_value`/`input_type`/`output_type`，返回匹配的 `RunOutputs` 列表。
    关键路径（三步）：1) 推导输出节点集合 2) 规范化输入 3) 调用 `graph.arun`。
    异常流：依赖 `graph.arun` 抛出；本函数不吞异常。
    排障入口：输出节点为空时检查 `output_type` 与节点 `id` 关系。
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
            logger.warning("InputValueRequest input_value cannot be None, defaulting to an empty string.")
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
    """校验 graph_data 与 tweaks 的结构并返回 nodes 列表。

    契约：输入必须为 dict；返回 `nodes` 列表。
    异常流：类型不匹配或缺少 `nodes` 时抛 `TypeError`。
    排障入口：确认 `graph_data` 是否包含 `data.nodes` 或 `nodes`。
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
    """将 tweaks 应用到节点模板数据。

    契约：`node_tweaks` 仅更新存在于模板中的字段；无对应字段将被忽略。
    关键路径：1) 定位 `template` 2) 匹配字段 3) 按类型写入值。
    异常流：模板结构异常时记录警告并返回。
    排障入口：日志关键字 `Template data for node`。
    """
    template_data = node.get("data", {}).get("node", {}).get("template")

    if not isinstance(template_data, dict):
        logger.warning(f"Template data for node {node.get('id')} should be a dictionary")
        return

    for tweak_name, tweak_value in node_tweaks.items():
        if tweak_name not in template_data:
            continue
        if tweak_name in template_data:
            if template_data[tweak_name]["type"] == "NestedDict":
                value = validate_and_repair_json(tweak_value)
                template_data[tweak_name]["value"] = value
            elif isinstance(tweak_value, dict):
                for k, v in tweak_value.items():
                    k_ = "file_path" if template_data[tweak_name]["type"] == "file" else k
                    template_data[tweak_name][k_] = v
            else:
                key = "file_path" if template_data[tweak_name]["type"] == "file" else "value"
                template_data[tweak_name][key] = tweak_value


def apply_tweaks_on_vertex(vertex: Vertex, node_tweaks: dict[str, Any]) -> None:
    """将 tweaks 直接写入运行期顶点参数。

    契约：仅当 `tweak_name` 存在于 `vertex.params` 时写入。
    异常流：无显式异常，忽略未匹配字段。
    """
    for tweak_name, tweak_value in node_tweaks.items():
        if tweak_name and tweak_value and tweak_name in vertex.params:
            vertex.params[tweak_name] = tweak_value


def process_tweaks(
    graph_data: dict[str, Any], tweaks: Tweaks | dict[str, dict[str, Any]], *, stream: bool = False
) -> dict[str, Any]:
    """将 tweaks 应用到图数据并返回更新后的结构。

    契约：支持以节点 `id` 或 `display_name` 作为 tweaks 键；返回被修改的 `graph_data`。
    关键路径（三步）：1) 规范化 tweaks 2) 构建节点映射 3) 逐节点应用覆盖。
    异常流：输入结构错误由 `validate_input` 抛 `TypeError`。
    排障入口：检查 `tweaks_dict` 是否包含预期节点标识。
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
    """在运行期将 tweaks 写入图内顶点参数。

    契约：以顶点 `id` 作为键写入；非 Vertex 或缺 `id` 时记录警告。
    异常流：无显式异常，非法节点会被跳过。
    排障入口：日志关键字 `Each node should be a Vertex`。
    """
    for vertex in graph.vertices:
        if isinstance(vertex, Vertex) and isinstance(vertex.id, str):
            node_id = vertex.id
            if node_tweaks := tweaks.get(node_id):
                apply_tweaks_on_vertex(vertex, node_tweaks)
        else:
            logger.warning("Each node should be a Vertex with an 'id' attribute of type str")

    return graph
