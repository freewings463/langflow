"""模块名称：图运行时核心实现

本模块实现图的构建、执行与调度，是 Langflow 运行时的核心。
使用场景：加载流程、构建顶点/边、执行图并产出运行结果。
主要功能包括：
- 图结构的构建与序列化
- 顶点构建、运行调度与缓存处理
- 循环检测、分层排序与条件路由

关键组件：
- Graph：图执行入口与运行时状态容器

设计背景：集中管理图结构与执行逻辑，统一运行时行为
注意事项：涉及并发与缓存，改动需谨慎评估副作用
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import copy
import json
import queue
import threading
import traceback
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from functools import partial
from itertools import chain
from typing import TYPE_CHECKING, Any, cast

from ag_ui.core import RunFinishedEvent, RunStartedEvent

from lfx.events.observability.lifecycle_events import observable
from lfx.exceptions.component import ComponentBuildError
from lfx.graph.edge.base import CycleEdge, Edge
from lfx.graph.graph.constants import Finish, lazy_load_vertex_dict
from lfx.graph.graph.runnable_vertices_manager import RunnableVerticesManager
from lfx.graph.graph.schema import GraphData, GraphDump, StartConfigDict, VertexBuildResult
from lfx.graph.graph.state_model import create_state_model_from_graph
from lfx.graph.graph.utils import (
    find_all_cycle_edges,
    find_cycle_vertices,
    find_start_component_id,
    get_sorted_vertices,
    process_flow,
    should_continue,
)
from lfx.graph.schema import InterfaceComponentTypes, RunOutputs
from lfx.graph.utils import log_vertex_build
from lfx.graph.vertex.base import Vertex, VertexStates
from lfx.graph.vertex.schema import NodeData, NodeTypeEnum
from lfx.graph.vertex.vertex_types import ComponentVertex, InterfaceVertex, StateVertex
from lfx.log.logger import LogConfig, configure, logger
from lfx.schema.dotdict import dotdict
from lfx.schema.schema import INPUT_FIELD_NAME, InputType, OutputValue
from lfx.services.cache.utils import CacheMiss
from lfx.services.deps import get_chat_service, get_tracing_service
from lfx.utils.async_helpers import run_until_complete

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterable
    from typing import Any

    from lfx.custom.custom_component.component import Component
    from lfx.events.event_manager import EventManager
    from lfx.graph.edge.schema import EdgeData
    from lfx.graph.schema import ResultData
    from lfx.schema.schema import InputValueRequest
    from lfx.services.chat.schema import GetCache, SetCache
    from lfx.services.tracing.service import TracingService


class Graph:
    """图执行核心。

    契约：管理顶点/边与运行时状态，提供构建、执行与序列化能力
    关键路径：1) 构建图结构 2) 调度执行 3) 汇总结果与状态
    副作用：可能触发缓存、日志与追踪
    """

    def __init__(
        self,
        start: Component | None = None,
        end: Component | None = None,
        flow_id: str | None = None,
        flow_name: str | None = None,
        description: str | None = None,
        user_id: str | None = None,
        log_config: LogConfig | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        """初始化 Graph 实例。

        关键路径（三步）：
        1) 校验 start/end 组合与 context 类型
        2) 初始化运行时结构与状态容器
        3) 可选预构建并进入可执行状态

        异常流：仅提供 start 或 end 时抛 `ValueError`；context 非 dict 抛 `TypeError`
        """
        if log_config:
            configure(**log_config)

        self._start = start
        self._state_model = None
        self._end = end
        self._prepared = False
        self._runs = 0
        self._updates = 0
        self.flow_id = flow_id
        self.flow_name = flow_name
        self.description = description
        self.user_id = user_id
        self._is_input_vertices: list[str] = []
        self._is_output_vertices: list[str] = []
        self._is_state_vertices: list[str] | None = None
        self.has_session_id_vertices: list[str] = []
        self._sorted_vertices_layers: list[list[str]] = []
        self._run_id = ""
        self._session_id = ""
        self._start_time = datetime.now(timezone.utc)
        self.inactivated_vertices: set = set()
        self.activated_vertices: list[str] = []
        self.vertices_layers: list[list[str]] = []
        self.vertices_to_run: set[str] = set()
        self.stop_vertex: str | None = None
        self.inactive_vertices: set = set()
        # 注意：条件路由与 ACTIVE/INACTIVE 循环管理分离。
        self.conditionally_excluded_vertices: set = set()  # 条件路由排除的顶点
        self.conditional_exclusion_sources: dict[str, set[str]] = {}  # 来源顶点 -> 被排除顶点
        self.edges: list[CycleEdge] = []
        self.vertices: list[Vertex] = []
        self.run_manager = RunnableVerticesManager()
        self._vertices: list[NodeData] = []
        self._edges: list[EdgeData] = []

        self.top_level_vertices: list[str] = []
        self.vertex_map: dict[str, Vertex] = {}
        self.predecessor_map: dict[str, list[str]] = defaultdict(list)
        self.successor_map: dict[str, list[str]] = defaultdict(list)
        self.in_degree_map: dict[str, int] = defaultdict(int)
        self.parent_child_map: dict[str, list[str]] = defaultdict(list)
        self._run_queue: deque[str] = deque()
        self._first_layer: list[str] = []
        self._lock: asyncio.Lock | None = None
        self.raw_graph_data: GraphData = {"nodes": [], "edges": []}
        self._is_cyclic: bool | None = None
        self._cycles: list[tuple[str, str]] | None = None
        self._cycle_vertices: set[str] | None = None
        self._call_order: list[str] = []
        self._snapshots: list[dict[str, Any]] = []
        self._end_trace_tasks: set[asyncio.Task] = set()

        if context and not isinstance(context, dict):
            msg = "Context must be a dictionary"
            raise TypeError(msg)
        self._context = dotdict(context or {})
        # 注意：追踪服务按需初始化，避免启动期开销。
        self._tracing_service: TracingService | None = None
        self._tracing_service_initialized = False
        if start is not None and end is not None:
            self._set_start_and_end(start, end)
            self.prepare(start_component_id=start.get_id())
        if (start is not None and end is None) or (start is None and end is not None):
            msg = "You must provide both input and output components"
            raise ValueError(msg)

    @property
    def lock(self):
        """延迟初始化 asyncio.Lock，避免绑定到错误事件循环。"""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    @property
    def context(self) -> dotdict:
        if isinstance(self._context, dotdict):
            return self._context
        return dotdict(self._context)

    @context.setter
    def context(self, value: dict[str, Any]):
        if not isinstance(value, dict):
            msg = "Context must be a dictionary"
            raise TypeError(msg)
        if isinstance(value, dict):
            value = dotdict(value)
        self._context = value

    @property
    def session_id(self):
        return self._session_id

    @session_id.setter
    def session_id(self, value: str):
        self._session_id = value

    @property
    def state_model(self):
        if not self._state_model:
            self._state_model = create_state_model_from_graph(self)
        return self._state_model

    def __add__(self, other):
        if not isinstance(other, Graph):
            msg = "Can only add Graph objects"
            raise TypeError(msg)
        # 注意：合并另一个图的顶点与边。
        new_instance = copy.deepcopy(self)
        for vertex in other.vertices:
            # 注意：添加顶点时会同步更新边。
            new_instance.add_vertex(vertex)
        new_instance.build_graph_maps(new_instance.edges)
        new_instance.define_vertices_lists()
        return new_instance

    def __iadd__(self, other):
        if not isinstance(other, Graph):
            msg = "Can only add Graph objects"
            raise TypeError(msg)
        # 注意：合并另一个图的顶点与边。
        for vertex in other.vertices:
            # 注意：添加顶点时会同步更新边。
            self.add_vertex(vertex)
        self.build_graph_maps(self.edges)
        self.define_vertices_lists()
        return self

    @property
    def tracing_service(self) -> TracingService | None:
        """按需初始化追踪服务。"""
        if not self._tracing_service_initialized:
            try:
                self._tracing_service = get_tracing_service()
            except Exception:  # noqa: BLE001
                logger.exception("Error getting tracing service")
                self._tracing_service = None
            self._tracing_service_initialized = True
        return self._tracing_service

    def dumps(
        self,
        name: str | None = None,
        description: str | None = None,
        endpoint_name: str | None = None,
    ) -> str:
        graph_dict = self.dump(name, description, endpoint_name)
        return json.dumps(graph_dict, indent=4, sort_keys=True)

    def dump(
        self, name: str | None = None, description: str | None = None, endpoint_name: str | None = None
    ) -> GraphDump:
        if self.raw_graph_data != {"nodes": [], "edges": []}:
            data_dict = self.raw_graph_data
        else:
            # 注意：需将顶点与边转换为可序列化结构。
            nodes = [node.to_data() for node in self.vertices]
            edges = [edge.to_data() for edge in self.edges]
            self.raw_graph_data = {"nodes": nodes, "edges": edges}
            data_dict = self.raw_graph_data
        graph_dict: GraphDump = {
            "data": data_dict,
            "is_component": len(data_dict.get("nodes", [])) == 1 and data_dict["edges"] == [],
        }
        if name:
            graph_dict["name"] = name
        elif name is None and self.flow_name:
            graph_dict["name"] = self.flow_name
        if description:
            graph_dict["description"] = description
        elif description is None and self.description:
            graph_dict["description"] = self.description
        graph_dict["endpoint_name"] = str(endpoint_name)
        return graph_dict

    def add_nodes_and_edges(self, nodes: list[NodeData], edges: list[EdgeData]) -> None:
        self._vertices = nodes
        self._edges = edges
        self.raw_graph_data = {"nodes": nodes, "edges": edges}
        self.top_level_vertices = []
        for vertex in self._vertices:
            if vertex_id := vertex.get("id"):
                self.top_level_vertices.append(vertex_id)
            if vertex_id in self.cycle_vertices:
                self.run_manager.add_to_cycle_vertices(vertex_id)
        self._graph_data = process_flow(self.raw_graph_data)

        self._vertices = self._graph_data["nodes"]
        self._edges = self._graph_data["edges"]
        self.initialize()

    def add_component(self, component: Component, component_id: str | None = None) -> str:
        component_id = component_id or component.get_id()
        if component_id in self.vertex_map:
            return component_id
        component.set_id(component_id)
        if component_id in self.vertex_map:
            msg = f"Component ID {component_id} already exists"
            raise ValueError(msg)
        frontend_node = component.to_frontend_node()
        self._vertices.append(frontend_node)
        vertex = self._create_vertex(frontend_node)
        vertex.add_component_instance(component)
        self._add_vertex(vertex)
        if component.get_edges():
            for edge in component.get_edges():
                self._add_edge(edge)

        if component.get_components():
            for _component in component.get_components():
                self.add_component(_component)

        return component_id

    def _set_start_and_end(self, start: Component, end: Component) -> None:
        if not hasattr(start, "to_frontend_node"):
            msg = f"start must be a Component. Got {type(start)}"
            raise TypeError(msg)
        if not hasattr(end, "to_frontend_node"):
            msg = f"end must be a Component. Got {type(end)}"
            raise TypeError(msg)
        self.add_component(start, start.get_id())
        self.add_component(end, end.get_id())

    def add_component_edge(self, source_id: str, output_input_tuple: tuple[str, str], target_id: str) -> None:
        source_vertex = self.get_vertex(source_id)
        if not isinstance(source_vertex, ComponentVertex):
            msg = f"Source vertex {source_id} is not a component vertex."
            raise TypeError(msg)
        target_vertex = self.get_vertex(target_id)
        if not isinstance(target_vertex, ComponentVertex):
            msg = f"Target vertex {target_id} is not a component vertex."
            raise TypeError(msg)
        output_name, input_name = output_input_tuple
        if source_vertex.custom_component is None:
            msg = f"Source vertex {source_id} does not have a custom component."
            raise ValueError(msg)
        if target_vertex.custom_component is None:
            msg = f"Target vertex {target_id} does not have a custom component."
            raise ValueError(msg)

        try:
            input_field = target_vertex.get_input(input_name)
            input_types = input_field.input_types
            input_field_type = str(input_field.field_type)
        except ValueError as e:
            input_field = target_vertex.data.get("node", {}).get("template", {}).get(input_name)
            if not input_field:
                msg = f"Input field {input_name} not found in target vertex {target_id}"
                raise ValueError(msg) from e
            input_types = input_field.get("input_types", [])
            input_field_type = input_field.get("type", "")

        edge_data: EdgeData = {
            "source": source_id,
            "target": target_id,
            "data": {
                "sourceHandle": {
                    "dataType": source_vertex.custom_component.name
                    or source_vertex.custom_component.__class__.__name__,
                    "id": source_vertex.id,
                    "name": output_name,
                    "output_types": source_vertex.get_output(output_name).types,
                },
                "targetHandle": {
                    "fieldName": input_name,
                    "id": target_vertex.id,
                    "inputTypes": input_types,
                    "type": input_field_type,
                },
            },
        }
        self._add_edge(edge_data)

    async def async_start(
        self,
        inputs: list[dict] | None = None,
        max_iterations: int | None = None,
        config: StartConfigDict | None = None,
        event_manager: EventManager | None = None,
        *,
        reset_output_values: bool = True,
    ):
        self.prepare()
        if reset_output_values:
            self._reset_all_output_values()

        # 注意：该方法作为异步生成器逐步产出结果。
        if config is not None:
            self.__apply_config(config)
        # 注意：记录每个顶点产出次数，支持循环上限判断。
        yielded_counts: dict[str, int] = defaultdict(int)

        while should_continue(yielded_counts, max_iterations):
            result = await self.astep(event_manager=event_manager, inputs=inputs)
            yield result
            if isinstance(result, Finish):
                return
            if hasattr(result, "vertex"):
                yielded_counts[result.vertex.id] += 1

        msg = "Max iterations reached"
        raise ValueError(msg)

    def _snapshot(self):
        return {
            "_run_queue": self._run_queue.copy(),
            "_first_layer": self._first_layer.copy(),
            "vertices_layers": copy.deepcopy(self.vertices_layers),
            "vertices_to_run": copy.deepcopy(self.vertices_to_run),
            "run_manager": copy.deepcopy(self.run_manager.to_dict()),
        }

    def __apply_config(self, config: StartConfigDict) -> None:
        for vertex in self.vertices:
            if vertex.custom_component is None:
                continue
            for output in vertex.custom_component.get_outputs_map().values():
                for key, value in config["output"].items():
                    setattr(output, key, value)

    def _reset_all_output_values(self) -> None:
        for vertex in self.vertices:
            if vertex.custom_component is None:
                continue
            vertex.custom_component.reset_all_output_values()

    def start(
        self,
        inputs: list[dict] | None = None,
        max_iterations: int | None = None,
        config: StartConfigDict | None = None,
        event_manager: EventManager | None = None,
    ) -> Generator:
        """同步执行图（通过线程内事件循环）。

        关键路径（三步）：
        1) 校验循环图的 max_iterations
        2) 在线程中驱动 async 生成器
        3) 从队列转发结果或异常
        """
        if self.is_cyclic and max_iterations is None:
            msg = "You must specify a max_iterations if the graph is cyclic"
            raise ValueError(msg)

        if config is not None:
            self.__apply_config(config)

        # 注意：队列用于线程间传递结果/异常。
        result_queue: queue.Queue[VertexBuildResult | Exception | None] = queue.Queue()

        # 注意：在线程内执行异步流程，避免主线程事件循环干扰。
        def run_async_code():
            # 注意：线程内独立事件循环。
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            try:
                async_gen = self.async_start(inputs, max_iterations, event_manager)

                while True:
                    try:
                        result = loop.run_until_complete(anext(async_gen))
                        result_queue.put(result)

                        if isinstance(result, Finish):
                            break

                    except StopAsyncIteration:
                        break
                    except ValueError as e:
                        result_queue.put(e)
                        break

            finally:
                # 注意：清理未完成任务，避免资源泄露。
                pending = asyncio.all_tasks(loop)
                if pending:
                    cleanup_future = asyncio.gather(*pending, return_exceptions=True)
                    loop.run_until_complete(cleanup_future)

                loop.close()
                result_queue.put(None)

        # 注意：启动后台线程执行。
        thread = threading.Thread(target=run_async_code)
        thread.start()

        # 注意：从队列持续产出结果。
        while True:
            result = result_queue.get()
            if result is None:
                break
            if isinstance(result, Exception):
                raise result
            yield result

        # 注意：等待线程结束。
        thread.join()

    def _add_edge(self, edge: EdgeData) -> None:
        self.add_edge(edge)
        source_id = edge["data"]["sourceHandle"]["id"]
        target_id = edge["data"]["targetHandle"]["id"]
        self.predecessor_map[target_id].append(source_id)
        self.successor_map[source_id].append(target_id)
        self.in_degree_map[target_id] += 1
        self.parent_child_map[source_id].append(target_id)

    def add_node(self, node: NodeData) -> None:
        self._vertices.append(node)

    def add_edge(self, edge: EdgeData) -> None:
        # 注意：避免重复边。
        if edge in self._edges:
            return
        self._edges.append(edge)

    def initialize(self) -> None:
        self._build_graph()
        self.build_graph_maps(self.edges)
        self.define_vertices_lists()

    @property
    def is_state_vertices(self) -> list[str]:
        """返回 state 顶点 ID 列表（惰性缓存）。"""
        if self._is_state_vertices is None:
            self._is_state_vertices = [vertex.id for vertex in self.vertices if vertex.is_state]
        return self._is_state_vertices

    def activate_state_vertices(self, name: str, caller: str) -> None:
        """激活指定状态相关的顶点与依赖。"""
        vertices_ids = set()
        new_predecessor_map = {}
        activated_vertices = []
        for vertex_id in self.is_state_vertices:
            caller_vertex = self.get_vertex(caller)
            vertex = self.get_vertex(vertex_id)
            if vertex_id == caller or vertex.display_name == caller_vertex.display_name:
                continue
            ctx_key = vertex.raw_params.get("context_key")
            if isinstance(ctx_key, str) and name in ctx_key and vertex_id != caller and isinstance(vertex, StateVertex):
                activated_vertices.append(vertex_id)
                vertices_ids.add(vertex_id)
                successors = self.get_all_successors(vertex, flat=True)
                # 注意：激活后需重建前驱映射以更新可运行状态。
                successors_predecessors = set()
                for sucessor in successors:
                    successors_predecessors.update(self.get_all_predecessors(sucessor))

                edges_set = set()
                for _vertex in [vertex, *successors, *successors_predecessors]:
                    edges_set.update(_vertex.edges)
                    if _vertex.state == VertexStates.INACTIVE:
                        _vertex.set_state("ACTIVE")

                    vertices_ids.add(_vertex.id)
                edges = list(edges_set)
                predecessor_map, _ = self.build_adjacency_maps(edges)
                new_predecessor_map.update(predecessor_map)

        vertices_ids.update(new_predecessor_map.keys())
        vertices_ids.update(v_id for value_list in new_predecessor_map.values() for v_id in value_list)

        self.activated_vertices = activated_vertices
        self.vertices_to_run.update(vertices_ids)
        self.run_manager.update_run_state(
            run_predecessors=new_predecessor_map,
            vertices_to_run=self.vertices_to_run,
        )

    def reset_activated_vertices(self) -> None:
        """清空已激活顶点集合。"""
        self.activated_vertices = []

    def validate_stream(self) -> None:
        """校验流式配置，避免相邻节点同时开启流式。"""
        for vertex in self.vertices:
            if vertex.params.get("stream") or vertex.params.get("streaming"):
                successors = self.get_all_successors(vertex)
                for successor in successors:
                    if successor.params.get("stream") or successor.params.get("streaming"):
                        msg = (
                            f"Components {vertex.display_name} and {successor.display_name} "
                            "are connected and both have stream or streaming set to True"
                        )
                        raise ValueError(msg)

    @property
    def first_layer(self):
        if self._first_layer is None:
            msg = "Graph not prepared. Call prepare() first."
            raise ValueError(msg)
        return self._first_layer

    @property
    def is_cyclic(self):
        """判断图是否存在环。"""
        if self._is_cyclic is None:
            self._is_cyclic = bool(self.cycle_vertices)
        return self._is_cyclic

    @property
    def run_id(self):
        """获取当前 run_id（未设置则报错）。"""
        if not self._run_id:
            msg = "Run ID not set"
            raise ValueError(msg)
        return self._run_id

    def set_run_id(self, run_id: uuid.UUID | str | None = None) -> None:
        """设置当前 run_id（缺省则生成）。"""
        if run_id is None:
            run_id = uuid.uuid4()

        self._run_id = str(run_id)

    async def initialize_run(self) -> None:
        if not self._run_id:
            self.set_run_id()
        if self.tracing_service:
            run_name = f"{self.flow_name} - {self.flow_id}"
            await self.tracing_service.start_tracers(
                run_id=uuid.UUID(self._run_id),
                run_name=run_name,
                user_id=self.user_id,
                session_id=self.session_id,
            )

    def _end_all_traces_async(self, outputs: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        task = asyncio.create_task(self.end_all_traces(outputs, error))
        self._end_trace_tasks.add(task)
        task.add_done_callback(self._end_trace_tasks.discard)

    def end_all_traces_in_context(
        self,
        outputs: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> Callable:
        # 注意：BackgroundTasks 运行在不同上下文，需复制 context。
        context = contextvars.copy_context()

        async def async_end_traces_func():
            await asyncio.create_task(self.end_all_traces(outputs, error), context=context)

        return async_end_traces_func

    async def end_all_traces(self, outputs: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        if not self.tracing_service:
            return
        self._end_time = datetime.now(timezone.utc)
        if outputs is None:
            outputs = {}
        outputs |= self.metadata
        await self.tracing_service.end_tracers(outputs, error)

    @property
    def sorted_vertices_layers(self) -> list[list[str]]:
        """返回按类型分层后的顶点列表。"""
        if not self._sorted_vertices_layers:
            self.sort_vertices()
        return self._sorted_vertices_layers

    def define_vertices_lists(self) -> None:
        """填充输入/输出/会话/状态顶点列表。"""
        for vertex in self.vertices:
            if vertex.is_input:
                self._is_input_vertices.append(vertex.id)
            if vertex.is_output:
                self._is_output_vertices.append(vertex.id)
            if vertex.has_session_id:
                self.has_session_id_vertices.append(vertex.id)
            if vertex.is_state:
                if self._is_state_vertices is None:
                    self._is_state_vertices = []
                self._is_state_vertices.append(vertex.id)

    def _set_inputs(self, input_components: list[str], inputs: dict[str, str], input_type: InputType | None) -> None:
        """根据组件列表与输入类型更新输入顶点参数。"""
        for vertex_id in self._is_input_vertices:
            vertex = self.get_vertex(vertex_id)
            # 注意：仅更新匹配的输入组件。
            if input_components and (vertex_id not in input_components and vertex.display_name not in input_components):
                continue
            # 注意：限定输入类型（如 chat/webhook）。
            if input_type is not None and input_type != "any" and input_type not in vertex.id.lower():
                continue
            if vertex is None:
                msg = f"Vertex {vertex_id} not found"
                raise ValueError(msg)
            vertex.update_raw_params(inputs, overwrite=True)

    @observable
    async def _run(
        self,
        *,
        inputs: dict[str, str],
        input_components: list[str],
        input_type: InputType | None,
        outputs: list[str],
        stream: bool,
        session_id: str,
        fallback_to_env_vars: bool,
        event_manager: EventManager | None = None,
    ) -> list[ResultData | None]:
        """执行一次图运行并返回结果列表。

        关键路径（三步）：
        1) 写入输入参数与会话信息
        2) 执行图并捕获异常
        3) 汇总输出结果
        """
        if input_components and not isinstance(input_components, list):
            msg = f"Invalid components value: {input_components}. Expected list"
            raise ValueError(msg)
        if input_components is None:
            input_components = []

        if not isinstance(inputs.get(INPUT_FIELD_NAME, ""), str):
            msg = f"Invalid input value: {inputs.get(INPUT_FIELD_NAME)}. Expected string"
            raise TypeError(msg)
        if inputs:
            self._set_inputs(input_components, inputs, input_type)
        # 注意：将 session_id 写入需要它的顶点。
        for vertex_id in self.has_session_id_vertices:
            vertex = self.get_vertex(vertex_id)
            if vertex is None:
                msg = f"Vertex {vertex_id} not found"
                raise ValueError(msg)
            vertex.update_raw_params({"session_id": session_id})
        # 注意：执行图的构建与运行流程。
        try:
            cache_service = get_chat_service()
            if cache_service and self.flow_id:
                await cache_service.set_cache(self.flow_id, self)
        except Exception:  # noqa: BLE001
            logger.exception("Error setting cache")

        try:
            # 注意：若存在 webhook 组件，优先作为起点。
            start_component_id = find_start_component_id(self._is_input_vertices)
            await self.process(
                start_component_id=start_component_id,
                fallback_to_env_vars=fallback_to_env_vars,
                event_manager=event_manager,
            )
            self.increment_run_count()
        except Exception as exc:
            self._end_all_traces_async(error=exc)
            msg = f"Error running graph: {exc}"
            raise ValueError(msg) from exc

        self._end_all_traces_async()
        # 注意：收集已构建顶点的输出结果。
        vertex_outputs = []
        for vertex in self.vertices:
            if not vertex.built:
                continue
            if vertex is None:
                msg = f"Vertex {vertex_id} not found"
                raise ValueError(msg)

            if not vertex.result and not stream and hasattr(vertex, "consume_async_generator"):
                await vertex.consume_async_generator()
            if (not outputs and vertex.is_output) or (vertex.display_name in outputs or vertex.id in outputs):
                vertex_outputs.append(vertex.result)

        return vertex_outputs

    async def arun(
        self,
        inputs: list[dict[str, str]],
        *,
        inputs_components: list[list[str]] | None = None,
        types: list[InputType | None] | None = None,
        outputs: list[str] | None = None,
        session_id: str | None = None,
        stream: bool = False,
        fallback_to_env_vars: bool = False,
        event_manager: EventManager | None = None,
    ) -> list[RunOutputs]:
        """异步运行图并返回输出列表。"""
        # 注意：输入可能是单个 dict 或多次运行的列表。
        vertex_outputs = []
        if not isinstance(inputs, list):
            inputs = [inputs]
        elif not inputs:
            inputs = [{}]
        # 注意：对齐 inputs 与 inputs_components 的长度。
        if inputs_components is None:
            inputs_components = []
        for _ in range(len(inputs) - len(inputs_components)):
            inputs_components.append([])
        if types is None:
            types = []
        if session_id:
            self.session_id = session_id
        for _ in range(len(inputs) - len(types)):
            types.append("chat")  # 默认输入类型为 chat
        for run_inputs, components, input_type in zip(inputs, inputs_components, types, strict=True):
            run_outputs = await self._run(
                inputs=run_inputs,
                input_components=components,
                input_type=input_type,
                outputs=outputs or [],
                stream=stream,
                session_id=session_id or "",
                fallback_to_env_vars=fallback_to_env_vars,
                event_manager=event_manager,
            )
            run_output_object = RunOutputs(inputs=run_inputs, outputs=run_outputs)
            await logger.adebug(f"Run outputs: {run_output_object}")
            vertex_outputs.append(run_output_object)
        return vertex_outputs

    def next_vertex_to_build(self):
        """返回待构建顶点的迭代器。"""
        yield from chain.from_iterable(self.vertices_layers)

    @property
    def metadata(self):
        """返回运行元数据。"""
        time_format = "%Y-%m-%d %H:%M:%S %Z"
        return {
            "start_time": self._start_time.strftime(time_format),
            "end_time": self._end_time.strftime(time_format),
            "time_elapsed": f"{(self._end_time - self._start_time).total_seconds()} seconds",
            "flow_id": self.flow_id,
            "flow_name": self.flow_name,
        }

    def build_graph_maps(self, edges: list[CycleEdge] | None = None, vertices: list[Vertex] | None = None) -> None:
        """构建图的邻接映射与父子映射。"""
        if edges is None:
            edges = self.edges

        if vertices is None:
            vertices = self.vertices

        self.predecessor_map, self.successor_map = self.build_adjacency_maps(edges)

        self.in_degree_map = self.build_in_degree(edges)
        self.parent_child_map = self.build_parent_child_map(vertices)

    def reset_inactivated_vertices(self) -> None:
        """恢复所有被标记为 INACTIVE 的顶点。"""
        for vertex_id in self.inactivated_vertices.copy():
            self.mark_vertex(vertex_id, "ACTIVE")
        self.inactivated_vertices = set()
        self.inactivated_vertices = set()

    def mark_all_vertices(self, state: str) -> None:
        """批量设置所有顶点状态。"""
        for vertex in self.vertices:
            vertex.set_state(state)

    def mark_vertex(self, vertex_id: str, state: str) -> None:
        """设置单个顶点状态。"""
        vertex = self.get_vertex(vertex_id)
        vertex.set_state(state)
        if state == VertexStates.INACTIVE:
            self.run_manager.remove_from_predecessors(vertex_id)

    def _mark_branch(
        self, vertex_id: str, state: str, visited: set | None = None, output_name: str | None = None
    ) -> set:
        """标记指定分支（递归）。"""
        if visited is None:
            visited = set()
        else:
            self.mark_vertex(vertex_id, state)
        if vertex_id in visited:
            return visited
        visited.add(vertex_id)

        for child_id in self.parent_child_map[vertex_id]:
            # 注意：仅标记通过指定输出名连接的子节点。
            if output_name:
                edge = self.get_edge(vertex_id, child_id)
                if edge and edge.source_handle.name != output_name:
                    continue
            self._mark_branch(child_id, state, visited)
        return visited

    def mark_branch(self, vertex_id: str, state: str, output_name: str | None = None) -> None:
        visited = self._mark_branch(vertex_id=vertex_id, state=state, output_name=output_name)
        new_predecessor_map, _ = self.build_adjacency_maps(self.edges)
        new_predecessor_map = {k: v for k, v in new_predecessor_map.items() if k in visited}
        if vertex_id in self.cycle_vertices:
            # 注意：仅保留环内且已运行过的依赖。
            new_predecessor_map = {
                k: [dep for dep in v if dep in self.cycle_vertices and dep in self.run_manager.ran_at_least_once]
                for k, v in new_predecessor_map.items()
            }
        self.run_manager.update_run_state(
            run_predecessors=new_predecessor_map,
            vertices_to_run=self.vertices_to_run,
        )

    def exclude_branch_conditionally(self, vertex_id: str, output_name: str | None = None) -> None:
        """条件路由排除分支（与 ACTIVE/INACTIVE 分离）。"""
        # 注意：清理该来源顶点之前的排除记录。
        if vertex_id in self.conditional_exclusion_sources:
            previous_exclusions = self.conditional_exclusion_sources[vertex_id]
            self.conditionally_excluded_vertices -= previous_exclusions
            del self.conditional_exclusion_sources[vertex_id]

        # 注意：记录本次排除的分支。
        visited: set[str] = set()
        excluded: set[str] = set()
        self._exclude_branch_conditionally(vertex_id, visited, excluded, output_name, skip_first=True)

        # 注意：记录来源顶点与排除集合的映射。
        if excluded:
            self.conditional_exclusion_sources[vertex_id] = excluded

    def _exclude_branch_conditionally(
        self, vertex_id: str, visited: set, excluded: set, output_name: str | None = None, *, skip_first: bool = False
    ) -> None:
        """递归排除分支顶点。"""
        if vertex_id in visited:
            return
        visited.add(vertex_id)

        # 注意：首节点为路由器自身，不参与排除。
        if not skip_first:
            self.conditionally_excluded_vertices.add(vertex_id)
            excluded.add(vertex_id)

        for child_id in self.parent_child_map[vertex_id]:
            # 注意：在路由器层仅沿指定输出分支前进。
            if skip_first and output_name:
                edge = self.get_edge(vertex_id, child_id)
                if edge and edge.source_handle.name != output_name:
                    continue
            # 注意：首层之后排除全部后代。
            self._exclude_branch_conditionally(child_id, visited, excluded, output_name=None, skip_first=False)

    def get_edge(self, source_id: str, target_id: str) -> CycleEdge | None:
        """获取两顶点之间的边（若存在）。"""
        for edge in self.edges:
            if edge.source_id == source_id and edge.target_id == target_id:
                return edge
        return None

    def build_parent_child_map(self, vertices: list[Vertex]):
        parent_child_map = defaultdict(list)
        for vertex in vertices:
            parent_child_map[vertex.id] = [child.id for child in self.get_successors(vertex)]
        return parent_child_map

    def increment_run_count(self) -> None:
        self._runs += 1

    def increment_update_count(self) -> None:
        self._updates += 1

    def __getstate__(self):
        # 注意：仅序列化运行必要字段；state_manager 为单例无需保存。
        return {
            "vertices": self.vertices,
            "edges": self.edges,
            "flow_id": self.flow_id,
            "flow_name": self.flow_name,
            "description": self.description,
            "user_id": self.user_id,
            "raw_graph_data": self.raw_graph_data,
            "top_level_vertices": self.top_level_vertices,
            "inactivated_vertices": self.inactivated_vertices,
            "run_manager": self.run_manager.to_dict(),
            "_run_id": self._run_id,
            "in_degree_map": self.in_degree_map,
            "parent_child_map": self.parent_child_map,
            "predecessor_map": self.predecessor_map,
            "successor_map": self.successor_map,
            "activated_vertices": self.activated_vertices,
            "vertices_layers": self.vertices_layers,
            "vertices_to_run": self.vertices_to_run,
            "stop_vertex": self.stop_vertex,
            "_run_queue": self._run_queue,
            "_first_layer": self._first_layer,
            "_vertices": self._vertices,
            "_edges": self._edges,
            "_is_input_vertices": self._is_input_vertices,
            "_is_output_vertices": self._is_output_vertices,
            "has_session_id_vertices": self.has_session_id_vertices,
            "_sorted_vertices_layers": self._sorted_vertices_layers,
        }

    def __deepcopy__(self, memo):
        # 注意：避免重复拷贝同一实例。
        if id(self) in memo:
            return memo[id(self)]

        if self._start is not None and self._end is not None:
            # 注意：深拷贝 start/end 组件。
            start_copy = copy.deepcopy(self._start, memo)
            end_copy = copy.deepcopy(self._end, memo)
            new_graph = type(self)(
                start_copy,
                end_copy,
                copy.deepcopy(self.flow_id, memo),
                copy.deepcopy(self.flow_name, memo),
                copy.deepcopy(self.user_id, memo),
            )
        else:
            # 注意：新图不带 start/end，但保留 flow 标识信息。
            new_graph = type(self)(
                None,
                None,
                copy.deepcopy(self.flow_id, memo),
                copy.deepcopy(self.flow_name, memo),
                copy.deepcopy(self.user_id, memo),
            )
            # 注意：深拷贝顶点与边。
            new_graph.add_nodes_and_edges(copy.deepcopy(self._vertices, memo), copy.deepcopy(self._edges, memo))

        # 注意：写入 memo 防止循环拷贝。
        memo[id(self)] = new_graph

        return new_graph

    def __setstate__(self, state):
        run_manager = state["run_manager"]
        if isinstance(run_manager, RunnableVerticesManager):
            state["run_manager"] = run_manager
        else:
            state["run_manager"] = RunnableVerticesManager.from_dict(run_manager)
        self.__dict__.update(state)
        self.vertex_map = {vertex.id: vertex for vertex in self.vertices}
        # 注意：追踪服务通过属性惰性初始化。
        self.set_run_id(self._run_id)

    @classmethod
    def from_payload(
        cls,
        payload: dict,
        flow_id: str | None = None,
        flow_name: str | None = None,
        user_id: str | None = None,
        context: dict | None = None,
    ) -> Graph:
        """从 payload 构建图实例。"""
        if "data" in payload:
            payload = payload["data"]
        try:
            vertices = payload["nodes"]
            edges = payload["edges"]
            graph = cls(flow_id=flow_id, flow_name=flow_name, user_id=user_id, context=context)
            graph.add_nodes_and_edges(vertices, edges)
        except KeyError as exc:
            logger.exception(exc)
            if "nodes" not in payload and "edges" not in payload:
                msg = f"Invalid payload. Expected keys 'nodes' and 'edges'. Found {list(payload.keys())}"
                raise ValueError(msg) from exc

            msg = f"Error while creating graph from payload: {exc}"
            raise ValueError(msg) from exc
        else:
            return graph

    def __eq__(self, /, other: object) -> bool:
        if not isinstance(other, Graph):
            return False
        return self.__repr__() == other.__repr__()

    # 注意：通过比较顶点的 __repr__ 更新本图的数据，保持结构一致。

    def update_edges_from_vertex(self, other_vertex: Vertex) -> None:
        """用另一个顶点的边更新当前图的边集合。"""
        new_edges = []
        for edge in self.edges:
            if other_vertex.id in {edge.source_id, edge.target_id}:
                continue
            new_edges.append(edge)
        new_edges += other_vertex.edges
        self.edges = new_edges

    def vertex_data_is_identical(self, vertex: Vertex, other_vertex: Vertex) -> bool:
        data_is_equivalent = vertex == other_vertex
        if not data_is_equivalent:
            return False
        return self.vertex_edges_are_identical(vertex, other_vertex)

    @staticmethod
    def vertex_edges_are_identical(vertex: Vertex, other_vertex: Vertex) -> bool:
        same_length = len(vertex.edges) == len(other_vertex.edges)
        if not same_length:
            return False
        return all(edge in other_vertex.edges for edge in vertex.edges)

    def update(self, other: Graph) -> Graph:
        # 注意：当前图已有顶点集合。
        existing_vertex_ids = {vertex.id for vertex in self.vertices}
        # 注意：目标图顶点集合。
        other_vertex_ids = set(other.vertex_map.keys())

        # 注意：新增顶点。
        new_vertex_ids = other_vertex_ids - existing_vertex_ids

        # 注意：被移除的顶点。
        removed_vertex_ids = existing_vertex_ids - other_vertex_ids

        # 注意：移除不再存在的顶点。
        for vertex_id in removed_vertex_ids:
            with contextlib.suppress(ValueError):
                self.remove_vertex(vertex_id)

        # 注意：先添加新顶点再更新边，避免边指向不存在的顶点。

        # 注意：添加新顶点。
        for vertex_id in new_vertex_ids:
            new_vertex = other.get_vertex(vertex_id)
            self._add_vertex(new_vertex)

        # 注意：更新新顶点关联的边。
        for vertex_id in new_vertex_ids:
            new_vertex = other.get_vertex(vertex_id)
            self._update_edges(new_vertex)
            # 注意：边来自新图，因此此处回填 graph 引用。
            new_vertex.graph = self

        # 注意：更新发生变化的顶点数据。
        for vertex_id in existing_vertex_ids.intersection(other_vertex_ids):
            self_vertex = self.get_vertex(vertex_id)
            other_vertex = other.get_vertex(vertex_id)
            # 注意：顶点不一致时进行更新。
            if not self.vertex_data_is_identical(self_vertex, other_vertex):
                self.update_vertex_from_another(self_vertex, other_vertex)

        self.build_graph_maps()
        self.define_vertices_lists()
        self.increment_update_count()
        return self

    def update_vertex_from_another(self, vertex: Vertex, other_vertex: Vertex) -> None:
        """用另一个顶点的数据覆盖当前顶点。"""
        vertex.full_data = other_vertex.full_data
        vertex.parse_data()
        # 注意：同步更新边信息。
        self.update_edges_from_vertex(other_vertex)
        vertex.params = {}
        vertex.build_params()
        vertex.graph = self
        # 注意：冻结顶点不重置结果与 built 状态。
        if not vertex.frozen:
            vertex.built = False
            vertex.result = None
            vertex.artifacts = {}
            vertex.set_top_level(self.top_level_vertices)
        self.reset_all_edges_of_vertex(vertex)

    def reset_all_edges_of_vertex(self, vertex: Vertex) -> None:
        """重建顶点关联边的参数配置。"""
        for edge in vertex.edges:
            for vid in [edge.source_id, edge.target_id]:
                if vid in self.vertex_map:
                    vertex_ = self.vertex_map[vid]
                    if not vertex_.frozen:
                        vertex_.build_params()

    def _add_vertex(self, vertex: Vertex) -> None:
        """向图中加入顶点（不更新边）。"""
        self.vertices.append(vertex)
        self.vertex_map[vertex.id] = vertex

    def add_vertex(self, vertex: Vertex) -> None:
        """向图中加入顶点并更新边。"""
        self._add_vertex(vertex)
        self._update_edges(vertex)

    def _update_edges(self, vertex: Vertex) -> None:
        """根据顶点边信息更新图边集合。"""
        # 注意：顶点自带边，需同步到图中。
        for edge in vertex.edges:
            if edge not in self.edges and edge.source_id in self.vertex_map and edge.target_id in self.vertex_map:
                self.edges.append(edge)

    def _build_graph(self) -> None:
        """根据节点/边数据构建图结构。"""
        self.vertices = self._build_vertices()
        self.vertex_map = {vertex.id: vertex for vertex in self.vertices}
        self.edges = self._build_edges()

        # 注意：此处先构建参数与组件，避免 LLM 顶点参数缺失。
        self._build_vertex_params()
        self._instantiate_components_in_vertices()
        self._set_cache_to_vertices_in_cycle()
        self._set_cache_if_listen_notify_components()
        for vertex in self.vertices:
            if vertex.id in self.cycle_vertices:
                self.run_manager.add_to_cycle_vertices(vertex.id)

    def _get_edges_as_list_of_tuples(self) -> list[tuple[str, str]]:
        """将边转换为 (source_id, target_id) 列表。"""
        return [(e["data"]["sourceHandle"]["id"], e["data"]["targetHandle"]["id"]) for e in self._edges]

    def _set_cache_if_listen_notify_components(self) -> None:
        """若存在 Listen/Notify 组件，则全局关闭输出缓存。"""
        has_listen_or_notify_component = any(
            vertex.id.split("-")[0] in {"Listen", "Notify"} for vertex in self.vertices
        )
        if has_listen_or_notify_component:
            for vertex in self.vertices:
                vertex.apply_on_outputs(lambda output_object: setattr(output_object, "cache", False))

    def _set_cache_to_vertices_in_cycle(self) -> None:
        """对环内顶点关闭输出缓存。"""
        edges = self._get_edges_as_list_of_tuples()
        cycle_vertices = set(find_cycle_vertices(edges))
        for vertex in self.vertices:
            if vertex.id in cycle_vertices:
                vertex.apply_on_outputs(lambda output_object: setattr(output_object, "cache", False))

    def _instantiate_components_in_vertices(self) -> None:
        """实例化所有顶点的组件。"""
        for vertex in self.vertices:
            vertex.instantiate_component(self.user_id)

    def remove_vertex(self, vertex_id: str) -> None:
        """从图中移除顶点及其相关边。"""
        vertex = self.get_vertex(vertex_id)
        if vertex is None:
            return
        self.vertices.remove(vertex)
        self.vertex_map.pop(vertex_id)
        self.edges = [edge for edge in self.edges if vertex_id not in {edge.source_id, edge.target_id}]

    def _build_vertex_params(self) -> None:
        """构建顶点参数。"""
        for vertex in self.vertices:
            vertex.build_params()

    def _validate_vertex(self, vertex: Vertex) -> bool:
        """校验顶点是否可参与执行。"""
        # 注意：无任何边连接的顶点视为无效。
        return len(self.get_vertex_edges(vertex.id)) > 0

    def get_vertex(self, vertex_id: str) -> Vertex:
        """按 ID 获取顶点（不存在则抛错）。"""
        try:
            return self.vertex_map[vertex_id]
        except KeyError as e:
            msg = f"Vertex {vertex_id} not found"
            raise ValueError(msg) from e

    def get_root_of_group_node(self, vertex_id: str) -> Vertex:
        """获取分组节点的根顶点。"""
        if vertex_id in self.top_level_vertices:
            # 注意：找出以该节点为 parent 的子节点。
            vertices = [vertex for vertex in self.vertices if vertex.parent_node_id == vertex_id]
            # 注意：选择后继不再落入子集的顶点作为根。
            for vertex in vertices:
                successors = self.get_all_successors(vertex, recursive=False)
                if not any(successor in vertices for successor in successors):
                    return vertex
        msg = f"Vertex {vertex_id} is not a top level vertex or no root vertex found"
        raise ValueError(msg)

    def get_next_in_queue(self):
        if not self._run_queue:
            return None
        return self._run_queue.popleft()

    def extend_run_queue(self, vertices: list[str]) -> None:
        self._run_queue.extend(vertices)

    async def astep(
        self,
        inputs: InputValueRequest | None = None,
        files: list[str] | None = None,
        user_id: str | None = None,
        event_manager: EventManager | None = None,
    ):
        if not self._prepared:
            msg = "Graph not prepared. Call prepare() first."
            raise ValueError(msg)
        if not self._run_queue:
            self._end_all_traces_async()
            return Finish()
        vertex_id = self.get_next_in_queue()
        if not vertex_id:
            msg = "No vertex to run"
            raise ValueError(msg)
        chat_service = get_chat_service()

        # 注意：chat 服务不可用时提供空实现缓存函数。
        if chat_service is not None:
            get_cache_func = chat_service.get_cache
            set_cache_func = chat_service.set_cache
        else:
            # 注意：测试或服务不可用时的空实现。
            async def get_cache_func(*args, **kwargs):  # noqa: ARG001
                return None

            async def set_cache_func(*args, **kwargs) -> bool:  # noqa: ARG001
                return True

        vertex_build_result = await self.build_vertex(
            vertex_id=vertex_id,
            user_id=user_id,
            inputs_dict=inputs.model_dump() if inputs and hasattr(inputs, "model_dump") else {},
            files=files,
            get_cache=get_cache_func,
            set_cache=set_cache_func,
            event_manager=event_manager,
        )

        next_runnable_vertices = await self.get_next_runnable_vertices(
            self.lock, vertex=vertex_build_result.vertex, cache=False
        )
        if self.stop_vertex and self.stop_vertex in next_runnable_vertices:
            next_runnable_vertices = [self.stop_vertex]
        self.extend_run_queue(next_runnable_vertices)
        self.reset_inactivated_vertices()
        self.reset_activated_vertices()

        if chat_service is not None:
            await chat_service.set_cache(str(self.flow_id or self._run_id), self)
        self._record_snapshot(vertex_id)
        return vertex_build_result

    def get_snapshot(self):
        return copy.deepcopy(
            {
                "run_manager": self.run_manager.to_dict(),
                "run_queue": self._run_queue,
                "vertices_layers": self.vertices_layers,
                "first_layer": self.first_layer,
                "inactive_vertices": self.inactive_vertices,
                "activated_vertices": self.activated_vertices,
            }
        )

    def _record_snapshot(self, vertex_id: str | None = None) -> None:
        self._snapshots.append(self.get_snapshot())
        if vertex_id:
            self._call_order.append(vertex_id)

    def step(
        self,
        inputs: InputValueRequest | None = None,
        files: list[str] | None = None,
        user_id: str | None = None,
    ):
        """同步执行下一顶点（包装 `astep`）。"""
        return run_until_complete(self.astep(inputs, files, user_id))

    async def build_vertex(
        self,
        vertex_id: str,
        *,
        get_cache: GetCache | None = None,
        set_cache: SetCache | None = None,
        inputs_dict: dict[str, str] | None = None,
        files: list[str] | None = None,
        user_id: str | None = None,
        fallback_to_env_vars: bool = False,
        event_manager: EventManager | None = None,
    ) -> VertexBuildResult:
        """构建单个顶点并返回构建结果。

        关键路径（三步）：
        1) 判断是否需要构建或读取缓存
        2) 执行顶点构建并更新状态
        3) 组装结果并返回
        """
        vertex = self.get_vertex(vertex_id)
        self.run_manager.add_to_vertices_being_run(vertex_id)
        try:
            params = ""
            should_build = False
            # 注意：Loop 顶点即使冻结也必须执行，以推进迭代。
            is_loop_component = vertex.display_name == "Loop" or vertex.is_loop
            if not vertex.frozen or is_loop_component:
                should_build = True
            else:
                # 注意：优先使用缓存结果。
                if get_cache is not None:
                    cached_result = await get_cache(key=vertex.id)
                else:
                    cached_result = CacheMiss()
                if isinstance(cached_result, CacheMiss):
                    should_build = True
                else:
                    try:
                        cached_vertex_dict = cached_result["result"]
                        # 注意：用缓存结果恢复顶点状态。
                        vertex.built = cached_vertex_dict["built"]
                        vertex.artifacts = cached_vertex_dict["artifacts"]
                        vertex.built_object = cached_vertex_dict["built_object"]
                        vertex.built_result = cached_vertex_dict["built_result"]
                        vertex.full_data = cached_vertex_dict["full_data"]
                        vertex.results = cached_vertex_dict["results"]
                        try:
                            vertex.finalize_build()

                            if vertex.result is not None:
                                vertex.result.used_frozen_result = True
                        except Exception:  # noqa: BLE001
                            logger.debug("Error finalizing build", exc_info=True)
                            vertex.built = False
                            should_build = True
                    except KeyError:
                        vertex.built = False
                        should_build = True

            if should_build:
                await vertex.build(
                    user_id=user_id,
                    inputs=inputs_dict,
                    fallback_to_env_vars=fallback_to_env_vars,
                    files=files,
                    event_manager=event_manager,
                )
                if set_cache is not None:
                    vertex_dict = {
                        "built": vertex.built,
                        "results": vertex.results,
                        "artifacts": vertex.artifacts,
                        "built_object": vertex.built_object,
                        "built_result": vertex.built_result,
                        "full_data": vertex.full_data,
                    }

                    await set_cache(key=vertex.id, data=vertex_dict)

        except Exception as exc:
            if not isinstance(exc, ComponentBuildError):
                await logger.aexception("Error building Component")
            raise

        if vertex.result is not None:
            params = f"{vertex.built_object_repr()}{params}"
            valid = True
            result_dict = vertex.result
            artifacts = vertex.artifacts
        else:
            msg = f"Error building Component: no result found for vertex {vertex_id}"
            raise ValueError(msg)

        return VertexBuildResult(
            result_dict=result_dict, params=params, valid=valid, artifacts=artifacts, vertex=vertex
        )

    def get_vertex_edges(
        self,
        vertex_id: str,
        *,
        is_target: bool | None = None,
        is_source: bool | None = None,
    ) -> list[CycleEdge]:
        """返回包含该顶点的边列表。"""
        # 注意：同时匹配 source/target。
        return [
            edge
            for edge in self.edges
            if (edge.source_id == vertex_id and is_source is not False)
            or (edge.target_id == vertex_id and is_target is not False)
        ]

    def get_vertices_with_target(self, vertex_id: str) -> list[Vertex]:
        """返回指向该顶点的上游顶点列表。"""
        vertices: list[Vertex] = []
        for edge in self.edges:
            if edge.target_id == vertex_id:
                vertex = self.get_vertex(edge.source_id)
                if vertex is None:
                    continue
                vertices.append(vertex)
        return vertices

    async def process(
        self,
        *,
        fallback_to_env_vars: bool,
        start_component_id: str | None = None,
        event_manager: EventManager | None = None,
    ) -> Graph:
        """按层并行处理图中的顶点。

        关键路径（三步）：
        1) 获取首层并初始化缓存函数
        2) 并行执行当前层任务
        3) 计算下一层并迭代
        """
        has_webhook_component = "webhook" in start_component_id.lower() if start_component_id else False
        first_layer = self.sort_vertices(start_component_id=start_component_id)
        vertex_task_run_count: dict[str, int] = {}
        to_process = deque(first_layer)
        layer_index = 0
        chat_service = get_chat_service()

        # 注意：chat 服务不可用时提供空实现缓存函数。
        if chat_service is not None:
            get_cache_func = chat_service.get_cache
            set_cache_func = chat_service.set_cache
        else:
            # 注意：测试或服务不可用时的空实现。
            async def get_cache_func(*args, **kwargs):  # noqa: ARG001
                return None

            async def set_cache_func(*args, **kwargs):
                pass

        await self.initialize_run()
        lock = asyncio.Lock()
        while to_process:
            current_batch = list(to_process)  # 注意：复制当前批次。
            to_process.clear()  # 注意：清空队列等待下一批。
            tasks = []
            for vertex_id in current_batch:
                vertex = self.get_vertex(vertex_id)
                task = asyncio.create_task(
                    self.build_vertex(
                        vertex_id=vertex_id,
                        user_id=self.user_id,
                        inputs_dict={},
                        fallback_to_env_vars=fallback_to_env_vars,
                        get_cache=get_cache_func,
                        set_cache=set_cache_func,
                        event_manager=event_manager,
                    ),
                    name=f"{vertex.id} Run {vertex_task_run_count.get(vertex_id, 0)}",
                )
                tasks.append(task)
                vertex_task_run_count[vertex_id] = vertex_task_run_count.get(vertex_id, 0) + 1

            await logger.adebug(f"Running layer {layer_index} with {len(tasks)} tasks, {current_batch}")
            try:
                next_runnable_vertices = await self._execute_tasks(
                    tasks, lock=lock, has_webhook_component=has_webhook_component
                )
            except Exception:
                await logger.aexception(f"Error executing tasks in layer {layer_index}")
                raise
            if not next_runnable_vertices:
                break
            to_process.extend(next_runnable_vertices)
            layer_index += 1

        await logger.adebug("Graph processing complete")
        return self

    def find_next_runnable_vertices(self, vertex_successors_ids: list[str]) -> list[str]:
        """根据后继列表推导下一批可运行顶点。"""
        next_runnable_vertices = set()
        for v_id in sorted(vertex_successors_ids):
            if not self.is_vertex_runnable(v_id):
                next_runnable_vertices.update(self.find_runnable_predecessors_for_successor(v_id))
            else:
                next_runnable_vertices.add(v_id)

        return sorted(next_runnable_vertices)

    async def get_next_runnable_vertices(self, lock: asyncio.Lock, vertex: Vertex, *, cache: bool = True) -> list[str]:
        """顶点完成后计算下一批可运行顶点。"""
        v_id = vertex.id
        v_successors_ids = vertex.successors_ids
        self.run_manager.ran_at_least_once.add(v_id)
        async with lock:
            self.run_manager.remove_vertex_from_runnables(v_id)
            next_runnable_vertices = self.find_next_runnable_vertices(v_successors_ids)

            for next_v_id in set(next_runnable_vertices):  # 注意：去重避免重复。
                if next_v_id == v_id:
                    next_runnable_vertices.remove(v_id)
                else:
                    self.run_manager.add_to_vertices_being_run(next_v_id)
            if cache and self.flow_id is not None:
                set_cache_coro = partial(get_chat_service().set_cache, key=self.flow_id)
                await set_cache_coro(data=self, lock=lock)
        if vertex.is_state:
            next_runnable_vertices.extend(self.activated_vertices)
        return next_runnable_vertices

    async def _log_vertex_build_from_exception(self, vertex_id: str, result: Exception) -> None:
        """记录顶点构建异常并写入日志事件。"""
        if isinstance(result, ComponentBuildError):
            params = result.message
            tb = result.formatted_traceback
        else:
            from lfx.utils.exceptions import format_exception_message

            tb = traceback.format_exc()
            await logger.aexception("Error building Component")

            params = format_exception_message(result)
        message = {"errorMessage": params, "stackTrace": tb}
        vertex = self.get_vertex(vertex_id)
        output_label = vertex.outputs[0]["name"] if vertex.outputs else "output"
        outputs = {output_label: OutputValue(message=message, type="error")}
        result_data_response = {
            "results": {},
            "outputs": outputs,
            "logs": {},
            "message": {},
            "artifacts": {},
            "timedelta": None,
            "duration": None,
            "used_frozen_result": False,
        }

        await log_vertex_build(
            flow_id=self.flow_id or "",
            vertex_id=vertex_id or "errors",
            valid=False,
            params=params,
            data=result_data_response,
            artifacts={},
        )

    async def _execute_tasks(
        self, tasks: list[asyncio.Task], lock: asyncio.Lock, *, has_webhook_component: bool = False
    ) -> list[str]:
        """并行执行任务并处理异常。"""
        results = []
        completed_tasks = await asyncio.gather(*tasks, return_exceptions=True)
        vertices: list[Vertex] = []

        for i, result in enumerate(completed_tasks):
            task_name = tasks[i].get_name()
            vertex_id = tasks[i].get_name().split(" ")[0]

            if isinstance(result, Exception):
                await logger.aerror(f"Task {task_name} failed with exception: {result}")
                if has_webhook_component:
                    await self._log_vertex_build_from_exception(vertex_id, result)

                # 注意：出现异常时取消剩余任务。
                for t in tasks[i + 1 :]:
                    t.cancel()
                raise result
            if isinstance(result, VertexBuildResult):
                if self.flow_id is not None:
                    await log_vertex_build(
                        flow_id=self.flow_id,
                        vertex_id=result.vertex.id,
                        valid=result.valid,
                        params=result.params,
                        data=result.result_dict,
                        artifacts=result.artifacts,
                    )

                vertices.append(result.vertex)
            else:
                msg = f"Invalid result from task {task_name}: {result}"
                raise TypeError(msg)

        for v in vertices:
            # 注意：执行过的顶点移出可运行集合，避免并行重复调度。
            self.run_manager.remove_vertex_from_runnables(v.id)

            await logger.adebug(f"Vertex {v.id}, result: {v.built_result}, object: {v.built_object}")

        for v in vertices:
            next_runnable_vertices = await self.get_next_runnable_vertices(lock, vertex=v, cache=False)
            results.extend(next_runnable_vertices)
        return list(set(results))

    def topological_sort(self) -> list[Vertex]:
        """对顶点执行拓扑排序。"""
        # 注意：状态 0=未访问，1=访问中，2=已访问。
        state = dict.fromkeys(self.vertices, 0)
        sorted_vertices = []

        def dfs(vertex) -> None:
            if state[vertex] == 1:
                # 注意：回边表示存在环。
                msg = "Graph contains a cycle, cannot perform topological sort"
                raise ValueError(msg)
            if state[vertex] == 0:
                state[vertex] = 1
                for edge in vertex.edges:
                    if edge.source_id == vertex.id:
                        dfs(self.get_vertex(edge.target_id))
                state[vertex] = 2
                sorted_vertices.append(vertex)

        # 注意：逐顶点 DFS。
        for vertex in self.vertices:
            if state[vertex] == 0:
                dfs(vertex)

        return list(reversed(sorted_vertices))

    def generator_build(self) -> Generator[Vertex, None, None]:
        """按拓扑顺序产出顶点。"""
        sorted_vertices = self.topological_sort()
        logger.debug("There are %s vertices in the graph", len(sorted_vertices))
        yield from sorted_vertices

    def get_predecessors(self, vertex):
        """返回顶点的直接前驱。"""
        return [self.get_vertex(source_id) for source_id in self.predecessor_map.get(vertex.id, [])]

    def get_all_successors(self, vertex: Vertex, *, recursive=True, flat=True, visited=None):
        """返回顶点后继（可递归/扁平/嵌套）。"""
        if visited is None:
            visited = set()

        # 注意：避免循环图重复访问。
        if vertex in visited:
            return []

        visited.add(vertex)

        successors = vertex.successors
        if not successors:
            return []

        successors_result = []

        for successor in successors:
            if recursive:
                next_successors = self.get_all_successors(successor, recursive=recursive, flat=flat, visited=visited)
                if flat:
                    successors_result.extend(next_successors)
                else:
                    successors_result.append(next_successors)
            if flat:
                successors_result.append(successor)
            else:
                successors_result.append([successor])

        if not flat and successors_result:
            return [successors, *successors_result]

        return successors_result

    def get_successors(self, vertex: Vertex) -> list[Vertex]:
        """返回顶点的直接后继。"""
        return [self.get_vertex(target_id) for target_id in self.successor_map.get(vertex.id, set())]

    def get_all_predecessors(self, vertex: Vertex, *, recursive: bool = True) -> list[Vertex]:
        """返回顶点的前驱（可递归）。"""
        _predecessors = self.predecessor_map.get(vertex.id, [])
        predecessors = [self.get_vertex(v_id) for v_id in _predecessors]
        if recursive:
            for predecessor in _predecessors:
                predecessors.extend(self.get_all_predecessors(self.get_vertex(predecessor), recursive=recursive))
        else:
            predecessors.extend([self.get_vertex(predecessor) for predecessor in _predecessors])
        return predecessors

    def get_vertex_neighbors(self, vertex: Vertex) -> dict[Vertex, int]:
        """返回相邻顶点及连接边数量。"""
        neighbors: dict[Vertex, int] = {}
        for edge in self.edges:
            if edge.source_id == vertex.id:
                neighbor = self.get_vertex(edge.target_id)
                if neighbor is None:
                    continue
                if neighbor not in neighbors:
                    neighbors[neighbor] = 0
                neighbors[neighbor] += 1
            elif edge.target_id == vertex.id:
                neighbor = self.get_vertex(edge.source_id)
                if neighbor is None:
                    continue
                if neighbor not in neighbors:
                    neighbors[neighbor] = 0
                neighbors[neighbor] += 1
        return neighbors

    @property
    def cycles(self):
        if self._cycles is None:
            if self._start is None:
                self._cycles = []
            else:
                entry_vertex = self._start.get_id()
                edges = [(e["data"]["sourceHandle"]["id"], e["data"]["targetHandle"]["id"]) for e in self._edges]
                self._cycles = find_all_cycle_edges(entry_vertex, edges)
        return self._cycles

    @property
    def cycle_vertices(self):
        if self._cycle_vertices is None:
            edges = self._get_edges_as_list_of_tuples()
            self._cycle_vertices = set(find_cycle_vertices(edges))
        return self._cycle_vertices

    def _build_edges(self) -> list[CycleEdge]:
        """根据边数据构建 Edge/CycleEdge。"""
        # 注意：先确保顶点存在，再构建边。
        edges: set[CycleEdge | Edge] = set()
        for edge in self._edges:
            new_edge = self.build_edge(edge)
            edges.add(new_edge)
        if self.vertices and not edges:
            logger.warning("Graph has vertices but no edges")
        return list(cast("Iterable[CycleEdge]", edges))

    def build_edge(self, edge: EdgeData) -> CycleEdge | Edge:
        source = self.get_vertex(edge["source"])
        target = self.get_vertex(edge["target"])

        if source is None:
            msg = f"Source vertex {edge['source']} not found"
            raise ValueError(msg)
        if target is None:
            msg = f"Target vertex {edge['target']} not found"
            raise ValueError(msg)
        if any(v in self.cycle_vertices for v in [source.id, target.id]):
            new_edge: CycleEdge | Edge = CycleEdge(source, target, edge)
        else:
            new_edge = Edge(source, target, edge)
        return new_edge

    @staticmethod
    def _get_vertex_class(node_type: str, node_base_type: str, node_id: str) -> type[Vertex]:
        """根据类型信息选择顶点类。"""
        # 注意：优先使用 node_base_type。
        node_name = node_id.split("-")[0]
        if node_name in InterfaceComponentTypes or node_type in InterfaceComponentTypes:
            return InterfaceVertex
        if node_name in {"SharedState", "Notify", "Listen"}:
            return StateVertex
        if node_base_type in lazy_load_vertex_dict.vertex_type_map:
            return lazy_load_vertex_dict.vertex_type_map[node_base_type]
        if node_name in lazy_load_vertex_dict.vertex_type_map:
            return lazy_load_vertex_dict.vertex_type_map[node_name]

        if node_type in lazy_load_vertex_dict.vertex_type_map:
            return lazy_load_vertex_dict.vertex_type_map[node_type]
        return Vertex

    def _build_vertices(self) -> list[Vertex]:
        """构建顶点对象列表。"""
        vertices: list[Vertex] = []
        for frontend_data in self._vertices:
            if frontend_data.get("type") == NodeTypeEnum.NoteNode:
                continue
            try:
                vertex_instance = self.get_vertex(frontend_data["id"])
            except ValueError:
                vertex_instance = self._create_vertex(frontend_data)
            vertices.append(vertex_instance)

        return vertices

    def _create_vertex(self, frontend_data: NodeData):
        vertex_data = frontend_data["data"]
        vertex_type: str = vertex_data["type"]
        vertex_base_type: str = vertex_data["node"]["template"]["_type"]
        if "id" not in vertex_data:
            msg = f"Vertex data for {vertex_data['display_name']} does not contain an id"
            raise ValueError(msg)

        vertex_class = self._get_vertex_class(vertex_type, vertex_base_type, vertex_data["id"])

        vertex_instance = vertex_class(frontend_data, graph=self)
        vertex_instance.set_top_level(self.top_level_vertices)
        return vertex_instance

    def prepare(self, stop_component_id: str | None = None, start_component_id: str | None = None):
        self.initialize()
        if stop_component_id and start_component_id:
            msg = "You can only provide one of stop_component_id or start_component_id"
            raise ValueError(msg)

        if stop_component_id or start_component_id:
            try:
                first_layer = self.sort_vertices(stop_component_id, start_component_id)
            except Exception:  # noqa: BLE001
                logger.exception("Error sorting vertices")
                first_layer = self.sort_vertices()
        else:
            first_layer = self.sort_vertices()

        for vertex_id in first_layer:
            self.run_manager.add_to_vertices_being_run(vertex_id)
            if vertex_id in self.cycle_vertices:
                self.run_manager.add_to_cycle_vertices(vertex_id)
        self._first_layer = sorted(first_layer)
        self._run_queue = deque(self._first_layer)
        self._prepared = True
        self._record_snapshot()
        return self

    @staticmethod
    def get_children_by_vertex_type(vertex: Vertex, vertex_type: str) -> list[Vertex]:
        """按类型筛选子节点。"""
        children = []
        vertex_types = [vertex.data["type"]]
        if "node" in vertex.data:
            vertex_types += vertex.data["node"]["base_classes"]
        if vertex_type in vertex_types:
            children.append(vertex)
        return children

    def __repr__(self) -> str:
        vertex_ids = [vertex.id for vertex in self.vertices]
        edges_repr = "\n".join([f"  {edge.source_id} --> {edge.target_id}" for edge in self.edges])

        return (
            f"Graph Representation:\n"
            f"----------------------\n"
            f"Vertices ({len(vertex_ids)}):\n"
            f"  {', '.join(map(str, vertex_ids))}\n\n"
            f"Edges ({len(self.edges)}):\n"
            f"{edges_repr}"
        )

    def __hash__(self) -> int:
        """基于字符串表示生成哈希。"""
        return hash(self.__repr__())

    def get_vertex_predecessors_ids(self, vertex_id: str) -> list[str]:
        """返回顶点前驱 ID。"""
        return [v.id for v in self.get_predecessors(self.get_vertex(vertex_id))]

    def get_vertex_successors_ids(self, vertex_id: str) -> list[str]:
        """返回顶点后继 ID。"""
        return [v.id for v in self.get_vertex(vertex_id).successors]

    def get_vertex_input_status(self, vertex_id: str) -> bool:
        """判断顶点是否为输入顶点。"""
        return self.get_vertex(vertex_id).is_input

    def get_parent_map(self) -> dict[str, str | None]:
        """返回所有顶点的 parent 映射。"""
        return {vertex.id: vertex.parent_node_id for vertex in self.vertices}

    def get_vertex_ids(self) -> list[str]:
        """返回图中所有顶点 ID。"""
        return [vertex.id for vertex in self.vertices]

    def get_terminal_nodes(self) -> list[str]:
        """返回终端节点（无出边）。"""
        return [vertex.id for vertex in self.vertices if not self.successor_map.get(vertex.id, [])]

    def sort_vertices(
        self,
        stop_component_id: str | None = None,
        start_component_id: str | None = None,
    ) -> list[str]:
        """对顶点进行分层排序。"""
        self.mark_all_vertices("ACTIVE")

        first_layer, remaining_layers = get_sorted_vertices(
            vertices_ids=self.get_vertex_ids(),
            cycle_vertices=self.cycle_vertices,
            stop_component_id=stop_component_id,
            start_component_id=start_component_id,
            graph_dict=self.__to_dict(),
            in_degree_map=self.in_degree_map,
            successor_map=self.successor_map,
            predecessor_map=self.predecessor_map,
            is_input_vertex=self.get_vertex_input_status,
            get_vertex_predecessors=self.get_vertex_predecessors_ids,
            get_vertex_successors=self.get_vertex_successors_ids,
            is_cyclic=self.is_cyclic,
        )

        self.increment_run_count()
        self._sorted_vertices_layers = [first_layer, *remaining_layers]
        self.vertices_layers = remaining_layers
        self.vertices_to_run = set(chain.from_iterable([first_layer, *remaining_layers]))
        self.build_run_map()
        self._first_layer = first_layer
        return first_layer

    @staticmethod
    def sort_interface_components_first(vertices_layers: list[list[str]]) -> list[list[str]]:
        """将包含 ChatInput/ChatOutput 的顶点置前。"""

        def contains_interface_component(vertex):
            return any(component.value in vertex for component in InterfaceComponentTypes)

        # 注意：对每层按接口组件优先排序。
        return [
            sorted(
                inner_list,
                key=lambda vertex: not contains_interface_component(vertex),
            )
            for inner_list in vertices_layers
        ]

    def sort_by_avg_build_time(self, vertices_layers: list[list[str]]) -> list[list[str]]:
        """按平均构建耗时升序排序顶点层。"""

        def sort_layer_by_avg_build_time(vertices_ids: list[str]) -> list[str]:
            """对单层按平均构建耗时排序。"""
            if len(vertices_ids) == 1:
                return vertices_ids
            vertices_ids.sort(key=lambda vertex_id: self.get_vertex(vertex_id).avg_build_time)

            return vertices_ids

        return [sort_layer_by_avg_build_time(layer) for layer in vertices_layers]

    def is_vertex_runnable(self, vertex_id: str) -> bool:
        """判断顶点是否可运行。"""
        # 注意：条件路由排除的顶点直接不可运行。
        if vertex_id in self.conditionally_excluded_vertices:
            return False
        is_active = self.get_vertex(vertex_id).is_active()
        is_loop = self.get_vertex(vertex_id).is_loop
        return self.run_manager.is_vertex_runnable(vertex_id, is_active=is_active, is_loop=is_loop)

    def build_run_map(self) -> None:
        """构建运行映射（前驱 -> 可解锁后继）。"""
        self.run_manager.build_run_map(predecessor_map=self.predecessor_map, vertices_to_run=self.vertices_to_run)

    def find_runnable_predecessors_for_successors(self, vertex_id: str) -> list[str]:
        """为后继顶点寻找可运行的前驱集合。"""
        runnable_vertices = []
        for successor_id in self.run_manager.run_map.get(vertex_id, []):
            runnable_vertices.extend(self.find_runnable_predecessors_for_successor(successor_id))

        return sorted(runnable_vertices)

    def find_runnable_predecessors_for_successor(self, vertex_id: str) -> list[str]:
        runnable_vertices = []
        visited = set()

        def find_runnable_predecessors(predecessor_id: str) -> None:
            if predecessor_id in visited:
                return
            visited.add(predecessor_id)

            if self.is_vertex_runnable(predecessor_id):
                runnable_vertices.append(predecessor_id)
            else:
                for pred_pred_id in self.run_manager.run_predecessors.get(predecessor_id, []):
                    find_runnable_predecessors(pred_pred_id)

        for predecessor_id in self.run_manager.run_predecessors.get(vertex_id, []):
            find_runnable_predecessors(predecessor_id)
        return runnable_vertices

    def remove_from_predecessors(self, vertex_id: str) -> None:
        self.run_manager.remove_from_predecessors(vertex_id)

    def remove_vertex_from_runnables(self, vertex_id: str) -> None:
        self.run_manager.remove_vertex_from_runnables(vertex_id)

    def get_top_level_vertices(self, vertices_ids):
        """根据顶点列表返回对应的顶层顶点 ID。"""
        top_level_vertices = []
        for vertex_id in vertices_ids:
            vertex = self.get_vertex(vertex_id)
            if vertex.parent_is_top_level:
                top_level_vertices.append(vertex.parent_node_id)
            else:
                top_level_vertices.append(vertex_id)
        return top_level_vertices

    def build_in_degree(self, edges: list[CycleEdge]) -> dict[str, int]:
        in_degree: dict[str, int] = defaultdict(int)

        for edge in edges:
            # 注意：同一组件重复连线仍计入入度。
            in_degree[edge.target_id] += 1
        for vertex in self.vertices:
            if vertex.id not in in_degree:
                in_degree[vertex.id] = 0
        return in_degree

    @staticmethod
    def build_adjacency_maps(edges: list[CycleEdge]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
        """构建前驱/后继映射。"""
        predecessor_map: dict[str, list[str]] = defaultdict(list)
        successor_map: dict[str, list[str]] = defaultdict(list)
        for edge in edges:
            predecessor_map[edge.target_id].append(edge.source_id)
            successor_map[edge.source_id].append(edge.target_id)
        return predecessor_map, successor_map

    def __to_dict(self) -> dict[str, dict[str, list[str]]]:
        """将图转换为后继/前驱字典。"""
        result: dict = {}
        for vertex in self.vertices:
            vertex_id = vertex.id
            sucessors = [i.id for i in self.get_all_successors(vertex)]
            predecessors = [i.id for i in self.get_predecessors(vertex)]
            result |= {vertex_id: {"successors": sucessors, "predecessors": predecessors}}
        return result

    def raw_event_metrics(self, optional_fields: dict | None = None) -> dict:
        if optional_fields is None:
            optional_fields = {}
        import time

        return {"timestamp": time.time(), **optional_fields}

    def before_callback_event(self, *args, **kwargs) -> RunStartedEvent:  # noqa: ARG002
        metrics = {}
        if hasattr(self, "raw_event_metrics"):
            metrics = self.raw_event_metrics({"total_components": len(self.vertices)})
        return RunStartedEvent(run_id=self._run_id, thread_id=self.flow_id, raw_event=metrics)

    def after_callback_event(self, result: Any = None, *args, **kwargs) -> RunFinishedEvent:  # noqa: ARG002
        metrics = {}
        if hasattr(self, "raw_event_metrics"):
            metrics = self.raw_event_metrics({"total_components": len(self.vertices)})
        return RunFinishedEvent(run_id=self._run_id, thread_id=self.flow_id, result=None, raw_event=metrics)
