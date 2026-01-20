"""模块名称：图结构与排序工具

本模块提供图结构处理、排序、循环检测与分层算法等通用工具。
使用场景：构建/执行图时进行预处理、裁剪与拓扑分层。
主要功能包括：
- 图裁剪（从起点/到终点）
- 循环检测与循环顶点识别
- 分层拓扑排序与层级优化
"""

import copy
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Any

import networkx as nx

PRIORITY_LIST_OF_INPUTS = ["webhook", "chat"]
MAX_CYCLE_APPEARANCES = 2


def find_start_component_id(vertices, *, is_webhook: bool = False):
    """按输入类型优先级选择起始组件 ID。

    契约：当 `is_webhook=True` 时仅匹配 `webhook`
    返回：匹配到的顶点 ID，未匹配则为 None
    """
    # 注意：Webhook 只允许 webhook 输入作为起点。
    priority_inputs = ["webhook"] if is_webhook else PRIORITY_LIST_OF_INPUTS

    # 注意：按优先级顺序遍历输入类型。
    for input_type_str in priority_inputs:
        component_id = next((vertex_id for vertex_id in vertices if input_type_str in vertex_id.lower()), None)
        if component_id:
            return component_id
    return None


def find_last_node(nodes, edges):
    """返回流程中的末端节点。"""
    source_ids = {edge["source"] for edge in edges}
    for node in nodes:
        if node["id"] not in source_ids:
            return node
    return None


def add_parent_node_id(nodes, parent_node_id) -> None:
    """为节点列表追加 `parent_node_id` 字段。"""
    for node in nodes:
        node["parent_node_id"] = parent_node_id


def add_frozen(nodes, frozen) -> None:
    """为节点列表设置 `frozen` 标记。"""
    for node in nodes:
        node["data"]["node"]["frozen"] = frozen


def ungroup_node(group_node_data, base_flow):
    """展开分组节点并合并回基础流程。

    契约：返回更新后的 nodes 列表，并原地修改 base_flow
    关键路径：1) 继承父节点信息 2) 重定向边 3) 更新模板
    """
    template, flow, frozen = (
        group_node_data["node"]["template"],
        group_node_data["node"]["flow"],
        group_node_data["node"].get("frozen", False),
    )
    parent_node_id = group_node_data["id"]

    g_nodes = flow["data"]["nodes"]
    add_parent_node_id(g_nodes, parent_node_id)
    add_frozen(g_nodes, frozen)
    g_edges = flow["data"]["edges"]

    # 注意：将边重定向到代理节点，保证输入输出一致。
    updated_edges = get_updated_edges(base_flow, g_nodes, g_edges, group_node_data["id"])

    # 注意：同步模板中的 proxy 指向。
    update_template(template, g_nodes)

    nodes = [n for n in base_flow["nodes"] if n["id"] != group_node_data["id"]] + g_nodes
    edges = (
        [e for e in base_flow["edges"] if e["target"] != group_node_data["id"] and e["source"] != group_node_data["id"]]
        + g_edges
        + updated_edges
    )

    base_flow["nodes"] = nodes
    base_flow["edges"] = edges

    return nodes


def process_flow(flow_object):
    """递归展开流程中的分组节点并返回新流程。"""
    cloned_flow = copy.deepcopy(flow_object)
    processed_nodes = set()  # 注意：避免重复处理。

    def process_node(node) -> None:
        node_id = node.get("id")

        # 注意：已处理节点直接跳过。
        if node_id in processed_nodes:
            return

        if node.get("data") and node["data"].get("node") and node["data"]["node"].get("flow"):
            process_flow(node["data"]["node"]["flow"]["data"])
            new_nodes = ungroup_node(node["data"], cloned_flow)
            # 注意：新增节点加入队列以继续处理。
            nodes_to_process.extend(new_nodes)

        # 注意：标记已处理，避免重复展开。
        processed_nodes.add(node_id)

    nodes_to_process = deque(cloned_flow["nodes"])

    while nodes_to_process:
        node = nodes_to_process.popleft()
        process_node(node)

    return cloned_flow


def update_template(template, g_nodes) -> None:
    """更新模板中的 proxy 字段并保留显示配置。"""
    for value in template.values():
        if not value.get("proxy"):
            continue
        proxy_dict = value["proxy"]
        field, id_ = proxy_dict["field"], proxy_dict["id"]
        node_index = next((i for i, n in enumerate(g_nodes) if n["id"] == id_), -1)
        if node_index != -1:
            display_name = None
            show = g_nodes[node_index]["data"]["node"]["template"][field]["show"]
            advanced = g_nodes[node_index]["data"]["node"]["template"][field]["advanced"]
            if "display_name" in g_nodes[node_index]["data"]["node"]["template"][field]:
                display_name = g_nodes[node_index]["data"]["node"]["template"][field]["display_name"]
            else:
                display_name = g_nodes[node_index]["data"]["node"]["template"][field]["name"]

            g_nodes[node_index]["data"]["node"]["template"][field] = value
            g_nodes[node_index]["data"]["node"]["template"][field]["show"] = show
            g_nodes[node_index]["data"]["node"]["template"][field]["advanced"] = advanced
            g_nodes[node_index]["data"]["node"]["template"][field]["display_name"] = display_name


def update_target_handle(new_edge, g_nodes):
    """更新边的 targetHandle（处理 proxy 目标）。"""
    target_handle = new_edge["data"]["targetHandle"]
    if proxy := target_handle.get("proxy"):
        proxy_id = proxy["id"]
        for node in g_nodes:
            if node["id"] == proxy_id:
                set_new_target_handle(proxy_id, new_edge, target_handle, node)
                break

    return new_edge


def set_new_target_handle(proxy_id, new_edge, target_handle, node) -> None:
    """为代理节点设置新的 targetHandle。"""
    new_edge["target"] = proxy_id
    type_ = target_handle.get("type")
    if type_ is None:
        msg = "The 'type' key must be present in target_handle."
        raise KeyError(msg)

    field = target_handle["proxy"]["field"]
    new_target_handle = {
        "fieldName": field,
        "type": type_,
        "id": proxy_id,
    }

    node_data = node["data"]["node"]
    if node_data.get("flow"):
        field_template_proxy = node_data["template"][field]["proxy"]
        new_target_handle["proxy"] = {
            "field": field_template_proxy["field"],
            "id": field_template_proxy["id"],
        }

    if input_types := target_handle.get("inputTypes"):
        new_target_handle["inputTypes"] = input_types

    new_edge["data"]["targetHandle"] = new_target_handle


def update_source_handle(new_edge, g_nodes, g_edges):
    """将 sourceHandle 指向子图的末端节点。"""
    last_node = copy.deepcopy(find_last_node(g_nodes, g_edges))
    new_edge["source"] = last_node["id"]
    new_source_handle = new_edge["data"]["sourceHandle"]
    new_source_handle["id"] = last_node["id"]
    new_edge["data"]["sourceHandle"] = new_source_handle
    return new_edge


def get_updated_edges(base_flow, g_nodes, g_edges, group_node_id):
    """根据分组节点重写边的 source/target。"""
    updated_edges = []
    for edge in base_flow["edges"]:
        new_edge = copy.deepcopy(edge)
        if new_edge["target"] == group_node_id:
            new_edge = update_target_handle(new_edge, g_nodes)

        if new_edge["source"] == group_node_id:
            new_edge = update_source_handle(new_edge, g_nodes, g_edges)

        if group_node_id in {edge["target"], edge["source"]}:
            updated_edges.append(new_edge)
    return updated_edges


def get_successors(graph: dict[str, dict[str, list[str]]], vertex_id: str) -> list[str]:
    successors_result = []
    stack = [vertex_id]
    visited = set()
    while stack:
        current_id = stack.pop()
        if current_id in visited:
            continue
        visited.add(current_id)
        if current_id != vertex_id:
            successors_result.append(current_id)
        stack.extend(graph[current_id]["successors"])
    return successors_result


def get_root_of_group_node(
    graph: dict[str, dict[str, list[str]]], vertex_id: str, parent_node_map: dict[str, str | None]
) -> str:
    """返回分组节点的根顶点。"""
    if vertex_id in parent_node_map.values():
        # 注意：找出所有以该节点为父节点的子节点。
        child_vertices = [v_id for v_id, parent_id in parent_node_map.items() if parent_id == vertex_id]

        # 注意：选择后继不再落入子集的顶点作为根。
        for child_id in child_vertices:
            successors = get_successors(graph, child_id)
            if not any(successor in child_vertices for successor in successors):
                return child_id

    msg = f"Vertex {vertex_id} is not a top level vertex or no root vertex found"
    raise ValueError(msg)


def sort_up_to_vertex(
    graph: dict[str, dict[str, list[str]]],
    vertex_id: str,
    *,
    parent_node_map: dict[str, str | None] | None = None,
    is_start: bool = False,
) -> list[str]:
    """裁剪到指定顶点并返回涉及顶点集合。"""
    try:
        stop_or_start_vertex = graph[vertex_id]
    except KeyError as e:
        if parent_node_map is None:
            msg = "Parent node map is required to find the root of a group node"
            raise ValueError(msg) from e
        vertex_id = get_root_of_group_node(graph=graph, vertex_id=vertex_id, parent_node_map=parent_node_map)
        if vertex_id not in graph:
            msg = f"Vertex {vertex_id} not found into graph"
            raise ValueError(msg) from e
        stop_or_start_vertex = graph[vertex_id]

    visited, excluded = set(), set()
    stack = [vertex_id]
    stop_predecessors = set(stop_or_start_vertex["predecessors"])

    while stack:
        current_id = stack.pop()
        if current_id in visited or current_id in excluded:
            continue

        visited.add(current_id)
        current_vertex = graph[current_id]

        stack.extend(current_vertex["predecessors"])

        if current_id == vertex_id or (current_id not in stop_predecessors and is_start):
            for successor_id in current_vertex["successors"]:
                if is_start:
                    stack.append(successor_id)
                else:
                    excluded.add(successor_id)
                for succ_id in get_successors(graph, successor_id):
                    if is_start:
                        stack.append(succ_id)
                    else:
                        excluded.add(succ_id)

    return list(visited)


def has_cycle(vertex_ids: list[str], edges: list[tuple[str, str]]) -> bool:
    """判断有向图是否存在环。"""
    # 注意：使用邻接表 + DFS 检测回边。
    graph = defaultdict(list)
    for u, v in edges:
        graph[u].append(v)

    def dfs(v, visited, rec_stack) -> bool:
        visited.add(v)
        rec_stack.add(v)

        for neighbor in graph[v]:
            if neighbor not in visited:
                if dfs(neighbor, visited, rec_stack):
                    return True
            elif neighbor in rec_stack:
                return True

        rec_stack.remove(v)
        return False

    visited: set[str] = set()
    rec_stack: set[str] = set()

    return any(vertex not in visited and dfs(vertex, visited, rec_stack) for vertex in vertex_ids)


def find_cycle_edge(entry_point: str, edges: list[tuple[str, str]]) -> tuple[str, str]:
    """从入口点查找导致环的边。"""
    # 注意：使用 DFS 回溯找出回边。
    graph = defaultdict(list)
    for u, v in edges:
        graph[u].append(v)

    def dfs(v, visited, rec_stack):
        visited.add(v)
        rec_stack.add(v)

        for neighbor in graph[v]:
            if neighbor not in visited:
                result = dfs(neighbor, visited, rec_stack)
                if result:
                    return result
            elif neighbor in rec_stack:
                return (v, neighbor)  # 注意：该边构成回路。

        rec_stack.remove(v)
        return None

    visited: set[str] = set()
    rec_stack: set[str] = set()

    return dfs(entry_point, visited, rec_stack)


def find_all_cycle_edges(entry_point: str, edges: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """从入口点查找所有导致环的边。"""
    # 注意：使用 DFS 标记回边。
    graph = defaultdict(list)
    for u, v in edges:
        graph[u].append(v)

    def dfs(v, visited, rec_stack, cycle_edges):
        visited.add(v)
        rec_stack.add(v)

        for neighbor in graph[v]:
            if neighbor not in visited:
                dfs(neighbor, visited, rec_stack, cycle_edges)
            elif neighbor in rec_stack:
                cycle_edges.append((v, neighbor))  # 注意：记录形成环的边。

        rec_stack.remove(v)

    visited: set[str] = set()
    rec_stack: set[str] = set()
    cycle_edges: list[tuple[str, str]] = []

    dfs(entry_point, visited, rec_stack, cycle_edges)

    return cycle_edges


def should_continue(yielded_counts: dict[str, int], max_iterations: int | None) -> bool:
    """判断循环是否继续执行（按最大迭代限制）。"""
    if max_iterations is None:
        return True
    return max(yielded_counts.values(), default=0) <= max_iterations


def find_cycle_vertices(edges):
    """返回参与任意环的顶点集合。"""
    graph = nx.DiGraph(edges)

    # 注意：通过强连通分量识别环参与者。
    cycle_vertices = set()

    for component in nx.strongly_connected_components(graph):
        if len(component) > 1 or graph.has_edge(tuple(component)[0], tuple(component)[0]):  # noqa: RUF015
            cycle_vertices.update(component)

    return sorted(cycle_vertices)


def layered_topological_sort(
    vertices_ids: set[str],
    in_degree_map: dict[str, int],
    successor_map: dict[str, list[str]],
    predecessor_map: dict[str, list[str]],
    start_id: str | None = None,
    cycle_vertices: set[str] | None = None,
    is_input_vertex: Callable[[str], bool] | None = None,  # noqa: ARG001
    *,
    is_cyclic: bool = False,
) -> list[list[str]]:
    """分层拓扑排序（支持循环图的启发式处理）。

    关键路径（三步）：
    1) 初始化队列（无入度或指定起点）
    2) 分层出队并更新入度
    3) 在循环场景下允许有限次数重复入层
    """
    # 注意：队列用于逐层消耗入度为 0 的顶点。
    cycle_vertices = cycle_vertices or set()
    in_degree_map = in_degree_map.copy()

    if is_cyclic and all(in_degree_map.values()):
        # 注意：所有顶点入度>0 表示存在环，需指定起点或兜底起点。
        if start_id is not None:
            queue = deque([start_id])
            # 注意：清空起点入度以允许循环遍历。
            in_degree_map[start_id] = 0
        else:
            chat_input = find_start_component_id(vertices_ids)
            if chat_input is None:
                queue = deque([next(iter(vertices_ids))])
                in_degree_map[next(iter(vertices_ids))] = 0
            else:
                queue = deque([chat_input])
                # 注意：清空 chat_input 入度以允许循环遍历。
                in_degree_map[chat_input] = 0
    else:
        # 注意：常规 DAG 以入度为 0 的顶点作为起点。
        queue = deque(
            vertex_id
            for vertex_id in vertices_ids
            if in_degree_map[vertex_id] == 0
            # 注意：曾尝试将输入顶点置前，但会导致 TextInput 误排在首位。
        )

    layers: list[list[str]] = []
    visited = set()
    cycle_counts = dict.fromkeys(vertices_ids, 0)
    current_layer = 0

    # 注意：首层单独处理，避免重复。
    if queue:
        layers.append([])  # 注意：初始化首层容器。
        first_layer_vertices = set()
        layer_size = len(queue)
        for _ in range(layer_size):
            vertex_id = queue.popleft()
            if vertex_id not in first_layer_vertices:
                first_layer_vertices.add(vertex_id)
                visited.add(vertex_id)
                cycle_counts[vertex_id] += 1
                layers[current_layer].append(vertex_id)

            for neighbor in successor_map[vertex_id]:
                # 注意：仅处理当前过滤后的顶点集合。
                if neighbor not in vertices_ids:
                    continue

                in_degree_map[neighbor] -= 1  # 注意：逻辑移除一条入边。
                if in_degree_map[neighbor] == 0:
                    queue.append(neighbor)

                elif in_degree_map[neighbor] > 0:
                    for predecessor in predecessor_map[neighbor]:
                        if (
                            predecessor not in queue
                            and predecessor not in first_layer_vertices
                            and (in_degree_map[predecessor] == 0 or predecessor in cycle_vertices)
                        ):
                            queue.append(predecessor)

        current_layer += 1  # 注意：进入下一层。

    # 注意：后续层允许循环顶点重复出现（有限次数）。
    while queue:
        layers.append([])  # 注意：初始化新层。
        layer_size = len(queue)
        for _ in range(layer_size):
            vertex_id = queue.popleft()
            if vertex_id not in visited or (is_cyclic and cycle_counts[vertex_id] < MAX_CYCLE_APPEARANCES):
                if vertex_id not in visited:
                    visited.add(vertex_id)
                cycle_counts[vertex_id] += 1
                layers[current_layer].append(vertex_id)

            for neighbor in successor_map[vertex_id]:
                # 注意：仅处理当前过滤后的顶点集合。
                if neighbor not in vertices_ids:
                    continue

                in_degree_map[neighbor] -= 1  # 注意：逻辑移除一条入边。
                if in_degree_map[neighbor] == 0 and neighbor not in visited:
                    queue.append(neighbor)
                    # # 注意：循环顶点可在需要时重置入度以允许再次出现。
                    # if neighbor in cycle_vertices and neighbor in visited:
                    #     in_degree_map[neighbor] = len(predecessor_map[neighbor])

                elif in_degree_map[neighbor] > 0:
                    for predecessor in predecessor_map[neighbor]:
                        if predecessor not in queue and (
                            predecessor not in visited
                            or (is_cyclic and cycle_counts[predecessor] < MAX_CYCLE_APPEARANCES)
                        ):
                            queue.append(predecessor)

        current_layer += 1  # 注意：进入下一层。

    # 注意：移除空层。
    return [layer for layer in layers if layer]


def refine_layers(
    initial_layers: list[list[str]],
    successor_map: dict[str, list[str]],
) -> list[list[str]]:
    """细化层级以满足依赖顺序。"""
    # 注意：先建立“顶点 -> 层索引”的映射。
    vertex_to_layer: dict[str, int] = {}
    for layer_index, layer in enumerate(initial_layers):
        for vertex in layer:
            vertex_to_layer[vertex] = layer_index

    refined_layers: list[list[str]] = [[] for _ in initial_layers]  # 注意：预建空层结构。
    new_layer_index_map = defaultdict(int)

    # 注意：根据依赖的最小层级决定新的层级索引。
    for vertex_id, deps in successor_map.items():
        indexes = [vertex_to_layer[dep] for dep in deps if dep in vertex_to_layer]
        new_layer_index = max(min(indexes, default=0) - 1, 0)
        new_layer_index_map[vertex_id] = new_layer_index

    for layer_index, layer in enumerate(initial_layers):
        for vertex_id in layer:
            # 注意：尽可能将顶点放入满足依赖的更高层。
            new_layer_index = new_layer_index_map[vertex_id]
            if new_layer_index > layer_index:
                refined_layers[new_layer_index].append(vertex_id)
                vertex_to_layer[vertex_id] = new_layer_index
            else:
                refined_layers[layer_index].append(vertex_id)

    # 注意：移除空层。
    return [layer for layer in refined_layers if layer]


def _max_dependency_index(
    vertex_id: str,
    index_map: dict[str, int],
    get_vertex_successors: Callable[[str], list[str]],
) -> int:
    """计算顶点依赖在同层中的最高索引。"""
    max_index = -1
    for successor_id in get_vertex_successors(vertex_id):
        successor_index = index_map.get(successor_id, -1)
        max_index = max(successor_index, max_index)
    return max_index


def _sort_single_layer_by_dependency(
    layer: list[str],
    get_vertex_successors: Callable[[str], list[str]],
) -> list[str]:
    """按依赖关系稳定排序单层顶点。"""
    # 注意：建立索引映射以加速依赖计算。
    index_map = {vertex: index for index, vertex in enumerate(layer)}
    dependency_cache: dict[str, int] = {}

    def max_dependency_index(vertex: str) -> int:
        if vertex in dependency_cache:
            return dependency_cache[vertex]
        max_index = index_map[vertex]
        for successor in get_vertex_successors(vertex):
            if successor in index_map:
                max_index = max(max_index, max_dependency_index(successor))

        dependency_cache[vertex] = max_index
        return max_index

    return sorted(layer, key=max_dependency_index, reverse=True)


def sort_layer_by_dependency(
    vertices_layers: list[list[str]],
    get_vertex_successors: Callable[[str], list[str]],
) -> list[list[str]]:
    """对每层按依赖关系排序，避免依赖指向后方。"""
    return [_sort_single_layer_by_dependency(layer, get_vertex_successors) for layer in vertices_layers]


def sort_chat_inputs_first(
    vertices_layers: list[list[str]],
    get_vertex_predecessors: Callable[[str], list[str]],
) -> list[list[str]]:
    """将 ChatInput 置于最前层（仅允许单个）。"""
    chat_input = None
    chat_input_layer_idx = None

    # 注意：确保全图只有一个 ChatInput。
    for layer_idx, layer in enumerate(vertices_layers):
        for vertex_id in layer:
            if "ChatInput" in vertex_id and get_vertex_predecessors(vertex_id):
                return vertices_layers
            if "ChatInput" in vertex_id:
                if chat_input is not None:
                    msg = "Only one chat input is allowed in the graph"
                    raise ValueError(msg)
                chat_input = vertex_id
                chat_input_layer_idx = layer_idx

    if not chat_input:
        return vertices_layers
    # 注意：ChatInput 已在首层时只需调整顺序。
    if chat_input_layer_idx == 0:
        if len(vertices_layers[0]) == 1:
            return vertices_layers

        # 注意：首层有其他节点时，拆出 ChatInput 单独成层。
        vertices_layers[0].remove(chat_input)
        return [[chat_input], *vertices_layers]

    # 注意：非首层时，将 ChatInput 提升至最前层。
    result_layers = []
    for layer in vertices_layers:
        layer_vertices = [v for v in layer if v != chat_input]
        if layer_vertices:
            result_layers.append(layer_vertices)

    return [[chat_input], *result_layers]


def get_sorted_vertices(
    vertices_ids: list[str],
    cycle_vertices: set[str],
    stop_component_id: str | None = None,
    start_component_id: str | None = None,
    graph_dict: dict[str, Any] | None = None,
    in_degree_map: dict[str, int] | None = None,
    successor_map: dict[str, list[str]] | None = None,
    predecessor_map: dict[str, list[str]] | None = None,
    is_input_vertex: Callable[[str], bool] | None = None,
    get_vertex_predecessors: Callable[[str], list[str]] | None = None,
    get_vertex_successors: Callable[[str], list[str]] | None = None,
    *,
    is_cyclic: bool = False,
) -> tuple[list[str], list[list[str]]]:
    """获取排序后的顶点层级。

    关键路径（三步）：
    1) 根据 start/stop 裁剪顶点集合
    2) 执行分层拓扑排序
    3) 调整 ChatInput 与依赖顺序
    """
    # 注意：若 stop 在环内，改用 start 作为裁剪起点。
    if stop_component_id in cycle_vertices:
        start_component_id = stop_component_id
        stop_component_id = None

    # 注意：必要时构建入度映射。
    if in_degree_map is None:
        in_degree_map = {}
        for vertex_id in vertices_ids:
            if get_vertex_predecessors is not None:
                in_degree_map[vertex_id] = len(get_vertex_predecessors(vertex_id))
            else:
                in_degree_map[vertex_id] = 0

    # 注意：必要时构建后继映射。
    if successor_map is None:
        successor_map = {}
        for vertex_id in vertices_ids:
            if get_vertex_successors is not None:
                successor_map[vertex_id] = get_vertex_successors(vertex_id)
            else:
                successor_map[vertex_id] = []

    # 注意：必要时构建前驱映射。
    if predecessor_map is None:
        predecessor_map = {}
        for vertex_id in vertices_ids:
            if get_vertex_predecessors is not None:
                predecessor_map[vertex_id] = get_vertex_predecessors(vertex_id)
            else:
                predecessor_map[vertex_id] = []

    # 注意：stop 存在时，仅保留其前驱链路。
    if stop_component_id is not None:
        filtered_vertices = filter_vertices_up_to_vertex(
            vertices_ids,
            stop_component_id,
            get_vertex_predecessors=get_vertex_predecessors,
            get_vertex_successors=get_vertex_successors,
            graph_dict=graph_dict,
        )
        vertices_ids = list(filtered_vertices)

    # 注意：start 存在时，保留与其连通的顶点集合。
    if start_component_id is not None:
        reachable_vertices = filter_vertices_from_vertex(
            vertices_ids,
            start_component_id,
            get_vertex_predecessors=get_vertex_predecessors,
            get_vertex_successors=get_vertex_successors,
            graph_dict=graph_dict,
        )
        connected_vertices = set()
        for vertex in reachable_vertices:
            connected_vertices.update(
                filter_vertices_up_to_vertex(
                    vertices_ids,
                    vertex,
                    get_vertex_predecessors=get_vertex_predecessors,
                    get_vertex_successors=get_vertex_successors,
                    graph_dict=graph_dict,
                )
            )
        vertices_ids = list(connected_vertices)

    layers = layered_topological_sort(
        vertices_ids=set(vertices_ids),
        in_degree_map=in_degree_map,
        successor_map=successor_map,
        predecessor_map=predecessor_map,
        start_id=start_component_id,
        is_input_vertex=is_input_vertex,
        cycle_vertices=cycle_vertices,
        is_cyclic=is_cyclic,
    )

    if not layers:
        return [], []

    first_layer = layers[0]
    remaining_layers = layers[1:]

    # 注意：确保 stop 组件位于末层（若存在）。
    if stop_component_id is not None and remaining_layers and stop_component_id not in remaining_layers[-1]:
        remaining_layers[-1].append(stop_component_id)

    # 注意：优先排序 ChatInput，并按依赖关系排序各层。
    all_layers = [first_layer, *remaining_layers]
    if get_vertex_predecessors is not None and start_component_id is None:
        all_layers = sort_chat_inputs_first(all_layers, get_vertex_predecessors)
    if get_vertex_successors is not None:
        all_layers = sort_layer_by_dependency(all_layers, get_vertex_successors)

    if not all_layers:
        return [], []

    return all_layers[0], all_layers[1:]


def filter_vertices_up_to_vertex(
    vertices_ids: list[str],
    vertex_id: str,
    get_vertex_predecessors: Callable[[str], list[str]] | None = None,
    get_vertex_successors: Callable[[str], list[str]] | None = None,
    graph_dict: dict[str, Any] | None = None,
) -> set[str]:
    """过滤出给定顶点的全部前驱集合。"""
    vertices_set = set(vertices_ids)
    if vertex_id not in vertices_set:
        return set()

    # 注意：未提供 getter 时使用 graph_dict 兜底。
    if get_vertex_predecessors is None:
        if graph_dict is None:
            msg = "Either get_vertex_predecessors or graph_dict must be provided"
            raise ValueError(msg)

        def get_vertex_predecessors(v):
            return graph_dict[v]["predecessors"]

    # 注意：用于过滤时可选构建后继 getter。
    if get_vertex_successors is None:
        if graph_dict is None:
            return set()

        def get_vertex_successors(v):
            return graph_dict[v]["successors"]

    filtered_vertices = {vertex_id}
    queue = deque([vertex_id])

    # 注意：BFS 向前遍历前驱链路。
    while queue:
        current_vertex = queue.popleft()
        for predecessor in get_vertex_predecessors(current_vertex):
            if predecessor in vertices_set and predecessor not in filtered_vertices:
                filtered_vertices.add(predecessor)
                queue.append(predecessor)

    return filtered_vertices


def filter_vertices_from_vertex(
    vertices_ids: list[str],
    vertex_id: str,
    get_vertex_predecessors: Callable[[str], list[str]] | None = None,
    get_vertex_successors: Callable[[str], list[str]] | None = None,
    graph_dict: dict[str, Any] | None = None,
) -> set[str]:
    """过滤出从给定顶点可达的后继集合。"""
    vertices_set = set(vertices_ids)
    if vertex_id not in vertices_set:
        return set()

    # 注意：未提供 getter 时使用 graph_dict 兜底。
    if get_vertex_predecessors is None:
        if graph_dict is None:
            msg = "Either get_vertex_predecessors or graph_dict must be provided"
            raise ValueError(msg)

        def get_vertex_predecessors(v):
            return graph_dict[v]["predecessors"]

    # 注意：用于过滤时可选构建后继 getter。
    if get_vertex_successors is None:
        if graph_dict is None:
            return set()

        def get_vertex_successors(v):
            return graph_dict[v]["successors"]

    filtered_vertices = {vertex_id}
    queue = deque([vertex_id])

    # 注意：BFS 向后遍历后继链路。
    while queue:
        current_vertex = queue.popleft()
        for successor in get_vertex_successors(current_vertex):
            if successor in vertices_set and successor not in filtered_vertices:
                filtered_vertices.add(successor)
                queue.append(successor)

    return filtered_vertices
