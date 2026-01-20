"""
模块名称：紧凑 Flow 展开器

本模块将 AI 生成的紧凑流程结构转换为 Langflow UI 需要的完整流程格式。
主要功能包括：
- 紧凑节点/边的解析与校验
- 根据组件模板补全节点结构
- 生成 ReactFlow 兼容的 handle 编码

关键组件：
- `expand_compact_flow`
- `_expand_node` / `_expand_edge`

设计背景：AI 产出的简化结构无法直接驱动前端，需要补齐模板与连线信息。
注意事项：组件类型未命中模板时会抛出异常。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CompactNode(BaseModel):
    """AI 紧凑节点结构。

    契约：`type` 为组件名，`values` 为字段值，`edited=True` 时需提供 `node` 全量数据。
    失败语义：字段缺失由 Pydantic 校验抛错。
    """

    id: str
    type: str
    values: dict[str, Any] = Field(default_factory=dict)
    # 注意：edited=True 时必须提供完整节点数据
    edited: bool = False
    node: dict[str, Any] | None = None


class CompactEdge(BaseModel):
    """AI 紧凑边结构。

    契约：`source/target` 为节点 ID，`*_output`/`*_input` 为端口名。
    失败语义：字段缺失由 Pydantic 校验抛错。
    """

    source: str
    source_output: str
    target: str
    target_input: str


class CompactFlowData(BaseModel):
    """紧凑流程数据结构。

    契约：包含 `nodes` 与 `edges` 列表。
    失败语义：字段缺失由 Pydantic 校验抛错。
    """

    nodes: list[CompactNode]
    edges: list[CompactEdge]


def _get_flat_components(all_types_dict: dict[str, Any]) -> dict[str, Any]:
    """将组件类型索引扁平化为 `{组件名: 模板}`。

    契约：输入嵌套组件字典，返回平铺字典。
    副作用：无。
    失败语义：非字典的层级会被忽略。
    """
    return {
        comp_name: comp_data
        for components in all_types_dict.values()
        if isinstance(components, dict)
        for comp_name, comp_data in components.items()
    }


def _expand_node(
    compact_node: CompactNode,
    flat_components: dict[str, Any],
) -> dict[str, Any]:
    """将紧凑节点展开为完整节点结构。

    契约：返回可直接渲染的 `genericNode` 数据结构。
    关键路径（三步）：
    1) 若 `edited=True`，直接使用完整节点数据
    2) 根据组件类型查找模板
    3) 合并 `values` 到模板并生成节点
    副作用：无（仅返回新结构）。
    失败语义：模板缺失或 edited 节点缺少数据抛 `ValueError`。

    决策：`edited=True` 时跳过模板合并
    问题：编辑过的节点已包含完整结构
    方案：直接复用节点数据
    代价：依赖上游保证节点结构完整
    重评：若上游无法保证则引入校验
    """
    if compact_node.edited:
        if not compact_node.node:
            msg = f"Node {compact_node.id} is marked as edited but has no node data"
            raise ValueError(msg)
        return {
            "id": compact_node.id,
            "type": "genericNode",
            "data": {
                "type": compact_node.type,
                "node": compact_node.node,
                "id": compact_node.id,
            },
        }

    if compact_node.type not in flat_components:
        msg = f"Component type '{compact_node.type}' not found in component index"
        raise ValueError(msg)

    # 性能：避免全量 deepcopy，仅复制会被修改的 template
    src_data = flat_components[compact_node.type]
    if "template" in src_data:
        template_data = src_data.copy()
        template_data["template"] = template = src_data["template"].copy()
    else:
        template_data = src_data.copy()
        template = template_data.get("template", {})

    # 注意：将用户 `values` 合并进模板字段
    for field_name, field_value in compact_node.values.items():
        t_value = template.get(field_name)
        if t_value is not None:
            if isinstance(t_value, dict):
                t_value["value"] = field_value
            else:
                template[field_name] = field_value
        else:
            # 注意：模板缺失字段时按新字段追加
            template[field_name] = {"value": field_value}

    return {
        "id": compact_node.id,
        "type": "genericNode",
        "data": {
            "type": compact_node.type,
            "node": template_data,
            "id": compact_node.id,
        },
    }


def _encode_handle(data: dict[str, Any]) -> str:
    """将 handle 字典编码为 ReactFlow 专用字符串。

    契约：输入字典，返回编码字符串。
    副作用：无。
    失败语义：序列化失败抛异常。
    """
    from lfx.utils.util import escape_json_dump

    return escape_json_dump(data)


def _build_source_handle_data(
    node_id: str,
    component_type: str,
    output_name: str,
    output_types: list[str],
) -> dict[str, Any]:
    """构建 sourceHandle 数据结构。

    契约：返回包含输出类型信息的字典。
    副作用：无。
    失败语义：不抛异常，依赖输入参数正确性。
    """
    return {
        "dataType": component_type,
        "id": node_id,
        "name": output_name,
        "output_types": output_types,
    }


def _build_target_handle_data(
    node_id: str,
    field_name: str,
    input_types: list[str],
    field_type: str,
) -> dict[str, Any]:
    """构建 targetHandle 数据结构。

    契约：返回包含输入类型与字段信息的字典。
    副作用：无。
    失败语义：不抛异常，依赖输入参数正确性。
    """
    return {
        "fieldName": field_name,
        "id": node_id,
        "inputTypes": input_types,
        "type": field_type,
    }


def _expand_edge(
    compact_edge: CompactEdge,
    expanded_nodes: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """将紧凑边展开为完整连线结构。

    契约：返回 ReactFlow 兼容的 edge 结构。
    关键路径（三步）：
    1) 获取源/目标节点并解析模板
    2) 推断输出/输入类型与字段类型
    3) 生成 handle 数据并编码
    副作用：无（仅返回新结构）。
    失败语义：节点缺失抛 `ValueError`。

    决策：输出类型缺失时回退到 `base_classes`
    问题：部分组件未声明 outputs
    方案：使用 `base_classes` 作为输出类型
    代价：类型信息可能不够精确
    重评：当所有组件补齐 outputs 后移除回退
    """
    source_node = expanded_nodes.get(compact_edge.source)
    target_node = expanded_nodes.get(compact_edge.target)

    if not source_node:
        msg = f"Source node '{compact_edge.source}' not found"
        raise ValueError(msg)
    if not target_node:
        msg = f"Target node '{compact_edge.target}' not found"
        raise ValueError(msg)

    source_node_data = source_node["data"]["node"]
    target_node_data = target_node["data"]["node"]

    source_outputs = source_node_data.get("outputs", [])
    source_output = next(
        (o for o in source_outputs if o.get("name") == compact_edge.source_output),
        None,
    )
    output_types = source_output.get("types", []) if source_output else []

    if not output_types:
        output_types = source_node_data.get("base_classes", [])

    target_template = target_node_data.get("template", {})
    target_field = target_template.get(compact_edge.target_input, {})
    input_types = target_field.get("input_types", [])
    field_type = target_field.get("type", "str") if isinstance(target_field, dict) else "str"
    if not input_types and isinstance(target_field, dict):
        input_types = [field_type]

    source_type = source_node["data"]["type"]

    source_handle_data = _build_source_handle_data(
        compact_edge.source,
        source_type,
        compact_edge.source_output,
        output_types,
    )
    target_handle_data = _build_target_handle_data(
        compact_edge.target,
        compact_edge.target_input,
        input_types,
        field_type,
    )

    source_handle_str = _encode_handle(source_handle_data)
    target_handle_str = _encode_handle(target_handle_data)

    edge_id = f"reactflow__edge-{compact_edge.source}{source_handle_str}-{compact_edge.target}{target_handle_str}"

    return {
        "source": compact_edge.source,
        "sourceHandle": source_handle_str,
        "target": compact_edge.target,
        "targetHandle": target_handle_str,
        "id": edge_id,
        "data": {
            "sourceHandle": source_handle_data,
            "targetHandle": target_handle_data,
        },
        "className": "",
        "selected": False,
        "animated": False,
    }


def expand_compact_flow(
    compact_data: dict[str, Any],
    all_types_dict: dict[str, Any],
) -> dict[str, Any]:
    """展开紧凑 Flow 为完整 Flow 数据。

    契约：返回包含 `nodes` 与 `edges` 的完整结构。
    关键路径（三步）：
    1) 解析并校验紧凑数据
    2) 扁平化组件模板并展开节点
    3) 解析连线并生成 edge
    副作用：无（仅返回新结构）。
    失败语义：紧凑数据格式错误或组件缺失时抛异常。

    决策：先展开节点再展开边
    问题：边解析依赖节点模板信息
    方案：先构建 `expanded_nodes` 再处理边
    代价：需要在内存中维护节点映射
    重评：若引入流式转换可调整顺序
    """
    flow_data = CompactFlowData(**compact_data)

    flat_components = _get_flat_components(all_types_dict)

    expanded_nodes: dict[str, dict[str, Any]] = {}
    for compact_node in flow_data.nodes:
        expanded = _expand_node(compact_node, flat_components)
        expanded_nodes[compact_node.id] = expanded

    expanded_edges = []
    for compact_edge in flow_data.edges:
        expanded = _expand_edge(compact_edge, expanded_nodes)
        expanded_edges.append(expanded)

    return {
        "nodes": list(expanded_nodes.values()),
        "edges": expanded_edges,
    }
