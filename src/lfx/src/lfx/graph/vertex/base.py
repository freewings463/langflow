"""
模块名称：图节点基类（Vertex）

模块目的：提供图执行节点的核心生命周期与参数构建逻辑。
使用场景：图运行时对节点进行构建、执行、结果传递与观测。
主要功能包括：
- 节点参数解析与依赖构建
- 组件实例化与结果聚合
- 结果与工件（artifacts）输出
- 事件与可观测性回调

关键组件：
- `Vertex`：节点核心实现
- `VertexStates`：节点状态枚举

设计背景：将组件执行、图依赖与事件打点统一在节点层处理。
注意：本模块涉及异步构建与外部调用，修改需关注并发与副作用。
"""

from __future__ import annotations

import asyncio
import inspect
import traceback
import types
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from enum import Enum
from typing import TYPE_CHECKING, Any

from ag_ui.core import StepFinishedEvent, StepStartedEvent

from lfx.events.observability.lifecycle_events import observable
from lfx.exceptions.component import ComponentBuildError
from lfx.graph.schema import INPUT_COMPONENTS, OUTPUT_COMPONENTS, InterfaceComponentTypes, ResultData
from lfx.graph.utils import UnbuiltObject, UnbuiltResult, log_transaction
from lfx.graph.vertex.param_handler import ParameterHandler
from lfx.interface import initialize
from lfx.interface.listing import lazy_load_dict
from lfx.log.logger import logger
from lfx.schema.artifact import ArtifactType
from lfx.schema.data import Data
from lfx.schema.message import Message
from lfx.schema.schema import INPUT_FIELD_NAME, OutputValue, build_output_logs
from lfx.utils.schemas import ChatOutputResponse
from lfx.utils.util import sync_to_async

if TYPE_CHECKING:
    from uuid import UUID

    from lfx.custom.custom_component.component import Component
    from lfx.events.event_manager import EventManager
    from lfx.graph.edge.base import CycleEdge, Edge
    from lfx.graph.graph.base import Graph
    from lfx.graph.vertex.schema import NodeData

    Log = dict


class VertexStates(str, Enum):
    """节点状态枚举。

    契约：状态值用于图运行时的调度与跳过逻辑。
    """

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    ERROR = "ERROR"


class Vertex:
    """图节点核心实现。

    契约：管理组件实例、参数、执行结果与工件输出。
    关键路径：`build` 驱动构建流程，`get_result` 提供结果读取。
    决策：统一在节点层做依赖构建与结果聚合。
    问题：图执行需要一致的生命周期与状态控制。
    方案：以节点为单位封装构建、执行与观测逻辑。
    代价：节点类复杂度较高，修改需谨慎。
    重评：当执行逻辑拆分为独立调度器时。
    """
    def __init__(
        self,
        data: NodeData,
        graph: Graph,
        *,
        base_type: str | None = None,
        is_task: bool = False,
        params: dict | None = None,
    ) -> None:
        """初始化节点并解析基础元信息。

        契约：输入节点数据与图引用，建立节点运行态状态。
        副作用：解析模板与输出定义，初始化参数与状态容器。
        异常流：节点数据结构缺失时抛 `KeyError`/`ValueError`。
        性能：初始化成本与模板规模线性相关。
        排障：检查 `data["data"]["node"]` 结构与 `template` 字段。
        """
        self._lock: asyncio.Lock | None = None
        self.will_stream = False
        self.updated_raw_params = False
        self.id: str = data["id"]
        self.base_name = self.id.split("-")[0]
        self.is_state = False
        type_strings = [self.id.split("-")[0], data["data"]["type"]]
        self.is_input = any(input_component_name in type_strings for input_component_name in INPUT_COMPONENTS)
        self.is_output = any(output_component_name in type_strings for output_component_name in OUTPUT_COMPONENTS)
        self._is_loop = None
        self.has_session_id = None
        self.custom_component = None
        self.has_external_input = False
        self.has_external_output = False
        self.graph = graph
        self.full_data = data.copy()
        self.base_type: str | None = base_type
        self.outputs: list[dict] = []
        self.parse_data()
        self.built_object: Any = UnbuiltObject()
        self.built_result: Any = None
        self.built = False
        self._successors_ids: list[str] | None = None
        self.artifacts: dict[str, Any] = {}
        self.artifacts_raw: dict[str, Any] | None = {}
        self.artifacts_type: dict[str, str] = {}
        self.steps: list[Callable] = [self._build]
        self.steps_ran: list[Callable] = []
        self.task_id: str | None = None
        self.is_task = is_task
        self.params = params or {}
        self.parent_node_id: str | None = self.full_data.get("parent_node_id")
        self.load_from_db_fields: list[str] = []
        self.parent_is_top_level = False
        self.layer = None
        self.result: ResultData | None = None
        self.results: dict[str, Any] = {}
        self.outputs_logs: dict[str, OutputValue] = {}
        self.logs: dict[str, list[Log]] = {}
        self.has_cycle_edges = False
        try:
            self.is_interface_component = self.vertex_type in InterfaceComponentTypes
        except ValueError:
            self.is_interface_component = False

        self.use_result = False
        self.build_times: list[float] = []
        self.state = VertexStates.ACTIVE
        self.output_names: list[str] = [
            output["name"] for output in self.outputs if isinstance(output, dict) and "name" in output
        ]
        self._incoming_edges: list[CycleEdge] | None = None
        self._outgoing_edges: list[CycleEdge] | None = None

    @property
    def lock(self):
        """延迟初始化并返回异步锁。

        契约：返回 `asyncio.Lock`，用于节点构建互斥。
        副作用：首次访问会创建锁实例。
        """
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    @property
    def is_loop(self) -> bool:
        """判断节点是否允许循环输出。"""
        if self._is_loop is None:
            self._is_loop = any(output.get("allows_loop", False) for output in self.outputs)
        return self._is_loop

    def set_input_value(self, name: str, value: Any) -> None:
        """设置组件输入值。

        契约：要求已绑定 `custom_component`。
        异常流：未绑定组件实例时抛 `ValueError`。
        """
        if self.custom_component is None:
            msg = f"Vertex {self.id} does not have a component instance."
            raise ValueError(msg)
        self.custom_component.set_input_value(name, value)

    def to_data(self):
        """返回节点原始数据结构。"""
        return self.full_data

    def add_component_instance(self, component_instance: Component) -> None:
        """绑定组件实例到节点。"""
        component_instance.set_vertex(self)
        self.custom_component = component_instance

    def add_result(self, name: str, result: Any) -> None:
        """记录单个输出结果。"""
        self.results[name] = result

    def set_state(self, state: str) -> None:
        """设置节点状态并同步图的失活集合。

        注意：仅当入度小于等于 1 时加入 `inactivated_vertices`。
        """
        self.state = VertexStates[state]
        if self.state == VertexStates.INACTIVE and self.graph.in_degree_map[self.id] <= 1:
            self.graph.inactivated_vertices.add(self.id)
        elif self.state == VertexStates.ACTIVE and self.id in self.graph.inactivated_vertices:
            self.graph.inactivated_vertices.remove(self.id)

    def is_active(self):
        """判断节点是否处于激活状态。"""
        return self.state == VertexStates.ACTIVE

    @property
    def avg_build_time(self):
        """返回平均构建耗时（秒）。"""
        return sum(self.build_times) / len(self.build_times) if self.build_times else 0

    def add_build_time(self, time) -> None:
        """追加一次构建耗时记录。"""
        self.build_times.append(time)

    def set_result(self, result: ResultData) -> None:
        """设置节点最终结果。"""
        self.result = result

    def get_built_result(self):
        """获取构建后的结果表示。

        契约：对接口类组件返回 `built_object`，其余返回结果字典。
        注意：`UnbuiltResult` 时返回空字典。
        """
        if self.is_interface_component and not isinstance(self.built_object, UnbuiltObject):
            result = self.built_object
            if not isinstance(result, dict | str) and hasattr(result, "content"):
                return result.content
            return result
        if isinstance(self.built_object, str):
            self.built_result = self.built_object

        if isinstance(self.built_result, UnbuiltResult):
            return {}

        return self.built_result if isinstance(self.built_result, dict) else {"result": self.built_result}

    def set_artifacts(self) -> None:
        """设置工件数据（由子类覆盖）。"""
        pass

    @property
    def edges(self) -> list[CycleEdge]:
        """返回与当前节点相连的边列表。"""
        return self.graph.get_vertex_edges(self.id)

    @property
    def outgoing_edges(self) -> list[CycleEdge]:
        """返回当前节点的出边列表。"""
        if self._outgoing_edges is None:
            self._outgoing_edges = [edge for edge in self.edges if edge.source_id == self.id]
        return self._outgoing_edges

    @property
    def incoming_edges(self) -> list[CycleEdge]:
        """返回当前节点的入边列表。"""
        if self._incoming_edges is None:
            self._incoming_edges = [edge for edge in self.edges if edge.target_id == self.id]
        return self._incoming_edges

    def get_incoming_edge_by_target_param(self, target_param: str) -> str | None:
        """根据目标参数名返回入边的源节点 ID。"""
        return next((edge.source_id for edge in self.incoming_edges if edge.target_param == target_param), None)

    @property
    def edges_source_names(self) -> set[str | None]:
        """返回所有边的 source handle 名称集合。"""
        return {edge.source_handle.name for edge in self.edges}

    @property
    def predecessors(self) -> list[Vertex]:
        """返回前驱节点列表。"""
        return self.graph.get_predecessors(self)

    @property
    def successors(self) -> list[Vertex]:
        """返回后继节点列表。"""
        return self.graph.get_successors(self)

    @property
    def successors_ids(self) -> list[str]:
        """返回后继节点 ID 列表。"""
        return self.graph.successor_map.get(self.id, [])

    def __getstate__(self):
        """序列化钩子：清理不可序列化字段。"""
        state = self.__dict__.copy()
        state["_lock"] = None
        state["built_object"] = None if isinstance(self.built_object, UnbuiltObject) else self.built_object
        state["built_result"] = None if isinstance(self.built_result, UnbuiltResult) else self.built_result
        return state

    def __setstate__(self, state):
        """反序列化钩子：恢复运行态字段。"""
        self.__dict__.update(state)
        self._lock = asyncio.Lock()
        self.built_object = state.get("built_object") or UnbuiltObject()
        self.built_result = state.get("built_result") or UnbuiltResult()

    def set_top_level(self, top_level_vertices: list[str]) -> None:
        """标记节点是否为顶层节点的子节点。"""
        self.parent_is_top_level = self.parent_node_id in top_level_vertices

    def parse_data(self) -> None:
        """解析节点模板与输出信息。"""
        self.data = self.full_data["data"]
        if self.data["node"]["template"]["_type"] == "Component":
            if "outputs" not in self.data["node"]:
                msg = f"Outputs not found for {self.display_name}"
                raise ValueError(msg)
            self.outputs = self.data["node"]["outputs"]
        else:
            self.outputs = self.data["node"].get("outputs", [])
            self.output = self.data["node"]["base_classes"]

        self.display_name: str = self.data["node"].get("display_name", self.id.split("-")[0])
        self.icon: str = self.data["node"].get("icon", self.id.split("-")[0])

        self.description: str = self.data["node"].get("description", "")
        self.frozen: bool = self.data["node"].get("frozen", False)

        self.is_input = self.data["node"].get("is_input") or self.is_input
        self.is_output = self.data["node"].get("is_output") or self.is_output
        template_dicts = {key: value for key, value in self.data["node"]["template"].items() if isinstance(value, dict)}

        self.has_session_id = "session_id" in template_dicts

        self.required_inputs: list[str] = []
        self.optional_inputs: list[str] = []
        for value_dict in template_dicts.values():
            list_to_append = self.required_inputs if value_dict.get("required") else self.optional_inputs

            if "type" in value_dict:
                list_to_append.append(value_dict["type"])
            if "input_types" in value_dict:
                list_to_append.extend(value_dict["input_types"])

        template_dict = self.data["node"]["template"]
        self.vertex_type = (
            self.data["type"]
            if "Tool" not in [type_ for out in self.outputs for type_ in out["types"]]
            or template_dict["_type"].islower()
            else template_dict["_type"]
        )

        if self.base_type is None:
            for base_type, value in lazy_load_dict.all_types_dict.items():
                if self.vertex_type in value:
                    self.base_type = base_type
                    break

    def get_value_from_output_names(self, key: str):
        """若 key 为输出名，返回对应的节点对象。"""
        if key in self.output_names:
            return self.graph.get_vertex(key)
        return None

    def get_value_from_template_dict(self, key: str):
        """从模板字典读取字段值。"""
        template_dict = self.data.get("node", {}).get("template", {})

        if key not in template_dict:
            msg = f"Key {key} not found in template dict"
            raise ValueError(msg)
        return template_dict.get(key, {}).get("value")

    def _set_params_from_normal_edge(self, params: dict, edge: Edge, template_dict: dict):
        """将边参数映射到节点参数字典。"""
        param_key = edge.target_param
        if param_key in template_dict and edge.target_id == self.id:
            if template_dict[param_key].get("list"):
                if param_key not in params:
                    params[param_key] = []
                params[param_key].append(self.graph.get_vertex(edge.source_id))
            elif edge.target_id == self.id:
                if isinstance(template_dict[param_key].get("value"), dict):
                    param_dict = template_dict[param_key]["value"]
                    if not param_dict or len(param_dict) != 1:
                        params[param_key] = self.graph.get_vertex(edge.source_id)
                    else:
                        params[param_key] = {key: self.graph.get_vertex(edge.source_id) for key in param_dict}

                else:
                    params[param_key] = self.graph.get_vertex(edge.source_id)
        elif param_key in self.output_names:
            params[param_key] = self.graph.get_vertex(edge.source_id)
        return params

    def build_params(self) -> None:
        """构建节点参数（边参数 + 字段参数）。

        契约：更新 `self.params`/`self.raw_params` 与 `load_from_db_fields`。
        异常流：图对象缺失时抛 `ValueError`。
        性能：参数构建耗时与边数量/字段数量线性相关。
        排障：检查 `ParameterHandler` 的字段解析日志。
        """
        if self.graph is None:
            msg = "Graph not found"
            raise ValueError(msg)

        if self.updated_raw_params:
            self.updated_raw_params = False
            return

        param_handler = ParameterHandler(self, storage_service=None)

        edge_params = param_handler.process_edge_parameters(self.edges)

        field_params, load_from_db_fields = param_handler.process_field_parameters()

        self.params = {**field_params, **edge_params}
        self.load_from_db_fields = load_from_db_fields
        self.raw_params = self.params.copy()

    def update_raw_params(self, new_params: Mapping[str, str | list[str]], *, overwrite: bool = False) -> None:
        """更新原始参数字典。

        契约：仅在 `overwrite=True` 或键已存在时更新参数。
        异常流：不抛异常；非法键会被丢弃（非覆盖模式）。
        注意：若原参数包含 Vertex 实例则直接返回不更新。
        """
        if not new_params:
            return
        if any(isinstance(self.raw_params.get(key), Vertex) for key in new_params):
            return
        if not overwrite:
            for key in new_params.copy():  # type: ignore[attr-defined]
                if key not in self.raw_params:
                    new_params.pop(key)  # type: ignore[attr-defined]
        self.raw_params.update(new_params)
        self.params = self.raw_params.copy()
        self.updated_raw_params = True

    def instantiate_component(self, user_id=None) -> None:
        """实例化并绑定组件对象。"""
        if not self.custom_component:
            self.custom_component, _ = initialize.loading.instantiate_class(
                user_id=user_id,
                vertex=self,
            )

    @observable
    async def _build(
        self,
        fallback_to_env_vars,
        user_id=None,
        event_manager: EventManager | None = None,
    ) -> None:
        """执行节点构建流程（异步）。

        关键路径（三步）：
        1) 构建依赖节点并更新参数
        2) 实例化组件并执行构建
        3) 校验构建结果并标记完成

        异常流：构建失败会抛 `ComponentBuildError` 或 `ValueError`。
        排障：查看日志关键字 `Error building Component`。
        """
        await logger.adebug(f"Building {self.display_name}")
        await self._build_each_vertex_in_params_dict()
        if self.base_type is None:
            msg = f"Base type for vertex {self.display_name} not found"
            raise ValueError(msg)

        if not self.custom_component:
            custom_component, custom_params = initialize.loading.instantiate_class(
                user_id=user_id, vertex=self, event_manager=event_manager
            )
        else:
            custom_component = self.custom_component
            if hasattr(self.custom_component, "set_event_manager"):
                self.custom_component.set_event_manager(event_manager)
            custom_params = initialize.loading.get_params(self.params)

        await self._build_results(
            custom_component=custom_component,
            custom_params=custom_params,
            fallback_to_env_vars=fallback_to_env_vars,
            base_type=self.base_type,
        )

        self._validate_built_object()

        self.built = True

    def extract_messages_from_artifacts(self, artifacts: dict[str, Any]) -> list[dict]:
        """从工件中提取消息列表。

        契约：输入 artifacts 字典，输出可序列化消息列表。
        异常流：缺失关键字段时返回空列表。
        """
        try:
            text = artifacts["text"]
            sender = artifacts.get("sender")
            sender_name = artifacts.get("sender_name")
            session_id = artifacts.get("session_id")
            stream_url = artifacts.get("stream_url")
            files = [{"path": file} if isinstance(file, str) else file for file in artifacts.get("files", [])]
            component_id = self.id
            type_ = self.artifacts_type

            if isinstance(sender_name, Data | Message):
                sender_name = sender_name.get_text()

            messages = [
                ChatOutputResponse(
                    message=text,
                    sender=sender,
                    sender_name=sender_name,
                    session_id=session_id,
                    stream_url=stream_url,
                    files=files,
                    component_id=component_id,
                    type=type_,
                ).model_dump(exclude_none=True)
            ]
        except KeyError:
            messages = []

        return messages

    def finalize_build(self) -> None:
        """整理构建结果并写入 `ResultData`。"""
        result_dict = self.get_built_result()
        self.set_artifacts()
        artifacts = self.artifacts_raw
        messages = self.extract_messages_from_artifacts(artifacts) if isinstance(artifacts, dict) else []
        result_dict = ResultData(
            results=result_dict,
            artifacts=artifacts,
            outputs=self.outputs_logs,
            logs=self.logs,
            messages=messages,
            component_display_name=self.display_name,
            component_id=self.id,
        )
        self.set_result(result_dict)

    async def _build_each_vertex_in_params_dict(self) -> None:
        """遍历参数中的节点并触发构建。"""
        for key, value in self.raw_params.items():
            if self._is_vertex(value):
                if value == self:
                    del self.params[key]
                    continue
                await self._build_vertex_and_update_params(
                    key,
                    value,
                )
            elif isinstance(value, list) and self._is_list_of_vertices(value):
                await self._build_list_of_vertices_and_update_params(key, value)
            elif isinstance(value, dict):
                await self._build_dict_and_update_params(
                    key,
                    value,
                )
            elif key not in self.params or self.updated_raw_params:
                self.params[key] = value

    async def _build_dict_and_update_params(
        self,
        key,
        vertices_dict: dict[str, Vertex],
    ) -> None:
        """处理字典类型参数并回填构建结果。"""
        for sub_key, value in vertices_dict.items():
            if not self._is_vertex(value):
                self.params[key][sub_key] = value
            else:
                result = await value.get_result(self, target_handle_name=key)
                self.params[key][sub_key] = result

    @staticmethod
    def _is_vertex(value):
        """判断值是否为 Vertex 实例。"""
        return isinstance(value, Vertex)

    def _is_list_of_vertices(self, value):
        """判断列表是否由 Vertex 实例组成。"""
        return all(self._is_vertex(vertex) for vertex in value)

    async def get_result(self, requester: Vertex, target_handle_name: str | None = None) -> Any:
        """获取节点结果（带并发互斥）。"""
        async with self.lock:
            return await self._get_result(requester, target_handle_name)

    async def _log_transaction_async(
        self,
        flow_id: str | UUID,
        source: Vertex,
        status: str,
        target: Vertex | None = None,
        error: str | Exception | None = None,
        outputs: dict[str, Any] | None = None,
    ) -> None:
        """异步记录执行事务。"""
        try:
            await log_transaction(flow_id, source, status, target, error, outputs)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"Error logging transaction: {exc!s}")

    async def _get_result(
        self,
        requester: Vertex,  # noqa: ARG002
        target_handle_name: str | None = None,  # noqa: ARG002
    ) -> Any:
        """读取构建结果或对象。"""
        if not self.built:
            msg = f"Component {self.display_name} has not been built yet"
            raise ValueError(msg)

        return self.built_result if self.use_result else self.built_object

    async def _build_vertex_and_update_params(self, key, vertex: Vertex) -> None:
        """构建单个节点并回填参数。"""
        result = await vertex.get_result(self, target_handle_name=key)
        self._handle_func(key, result)
        if isinstance(result, list):
            self._extend_params_list_with_result(key, result)
        self.params[key] = result

    async def _build_list_of_vertices_and_update_params(
        self,
        key,
        vertices: list[Vertex],
    ) -> None:
        """构建节点列表并回填参数。"""
        self.params[key] = []
        for vertex in vertices:
            result = await vertex.get_result(self, target_handle_name=key)
            if not isinstance(self.params[key], list):
                self.params[key] = [self.params[key]]

            if isinstance(result, list):
                self.params[key].extend(result)
            else:
                try:
                    if self.params[key] == result:
                        continue

                    self.params[key].append(result)
                except AttributeError as e:
                    await logger.aexception(e)
                    msg = (
                        f"Params {key} ({self.params[key]}) is not a list and cannot be extended with {result}"
                        f"Error building Component {self.display_name}: \n\n{e}"
                    )
                    raise ValueError(msg) from e

    def _handle_func(self, key, result) -> None:
        """处理 `func` 参数并设置协程包装。"""
        if key == "func":
            if not isinstance(result, types.FunctionType):
                if hasattr(result, "run"):
                    result = result.run
                elif hasattr(result, "get_function"):
                    result = result.get_function()
            elif inspect.iscoroutinefunction(result):
                self.params["coroutine"] = result
            else:
                self.params["coroutine"] = sync_to_async(result)

    def _extend_params_list_with_result(self, key, result) -> None:
        """若参数为列表则扩展结果。"""
        if isinstance(self.params[key], list):
            self.params[key].extend(result)

    async def _build_results(
        self,
        custom_component,
        custom_params,
        base_type: str,
        *,
        fallback_to_env_vars=False,
    ) -> None:
        """调用组件构建并更新输出日志与工件。"""
        try:
            result = await initialize.loading.get_instance_results(
                custom_component=custom_component,
                custom_params=custom_params,
                vertex=self,
                fallback_to_env_vars=fallback_to_env_vars,
                base_type=base_type,
            )

            self.outputs_logs = build_output_logs(self, result)

            self._update_built_object_and_artifacts(result)
        except Exception as exc:
            tb = traceback.format_exc()
            await logger.aexception(exc)
            flow_id = self.graph.flow_id
            if flow_id:
                await self._log_transaction_async(
                    str(flow_id), source=self, target=None, status="error", error=str(exc)
                )
            msg = f"Error building Component {self.display_name}: \n\n{exc}"
            raise ComponentBuildError(msg, tb) from exc

    def _update_built_object_and_artifacts(self, result: Any | tuple[Any, dict] | tuple[Component, Any, dict]) -> None:
        """更新构建对象与工件输出。"""
        if isinstance(result, tuple):
            if len(result) == 2:  # noqa: PLR2004
                self.built_object, self.artifacts = result
            elif len(result) == 3:  # noqa: PLR2004
                self.custom_component, self.built_object, self.artifacts = result
                self.logs = self.custom_component.get_output_logs()
                self.artifacts_raw = self.artifacts.get("raw", None)
                self.artifacts_type = {
                    self.outputs[0]["name"]: self.artifacts.get("type", None) or ArtifactType.UNKNOWN.value
                }
                self.artifacts = {self.outputs[0]["name"]: self.artifacts}
        else:
            self.built_object = result

    def _validate_built_object(self) -> None:
        """校验构建结果有效性。"""
        if isinstance(self.built_object, UnbuiltObject):
            msg = f"{self.display_name}: {self.built_object_repr()}"
            raise TypeError(msg)
        if self.built_object is None:
            message = f"{self.display_name} returned None."
            if self.base_type == "custom_components":
                message += " Make sure your build method returns a component."

            logger.warning(message)
        elif isinstance(self.built_object, Iterator | AsyncIterator):
            if self.display_name == "Text Output":
                msg = f"You are trying to stream to a {self.display_name}. Try using a Chat Output instead."
                raise ValueError(msg)

    def _reset(self) -> None:
        """重置节点构建状态。"""
        self.built = False
        self.built_object = UnbuiltObject()
        self.built_result = UnbuiltResult()
        self.artifacts = {}
        self.steps_ran = []
        self.build_params()

    def _is_chat_input(self) -> bool:
        """是否为聊天输入节点（基类默认 False）。"""
        return False

    def build_inactive(self) -> None:
        """对失活节点构建，直接置空结果。"""
        self.built = True
        self.built_object = None
        self.built_result = None

    async def build(
        self,
        user_id=None,
        inputs: dict[str, Any] | None = None,
        files: list[str] | None = None,
        requester: Vertex | None = None,
        event_manager: EventManager | None = None,
        **kwargs,
    ) -> Any:
        """构建节点并返回请求方可用结果。

        关键路径（三步）：
        1) 懒加载组件并获取锁
        2) 处理会话/输入参数并执行构建步骤
        3) 生成结果与日志并返回给请求方

        异常流：构建失败抛 `ComponentBuildError` 或 `ValueError`。
        排障：查看日志关键字 `Error building Component`。
        """
        from lfx.interface.components import ensure_component_loaded
        from lfx.services.deps import get_settings_service

        settings_service = get_settings_service()
        if settings_service and settings_service.settings.lazy_load_components:
            component_name = self.id.split("-")[0]
            await ensure_component_loaded(self.vertex_type, component_name, settings_service)

        async with self.lock:
            if self.state == VertexStates.INACTIVE:
                self.build_inactive()
                return None

            is_loop_component = self.display_name == "Loop" or self.is_loop
            if self.frozen and self.built and not is_loop_component:
                return await self.get_requester_result(requester)
            if self.built and requester is not None:
                return await self.get_requester_result(requester)
            self._reset()
            if inputs is not None and "session" in inputs and inputs["session"] is not None and self.has_session_id:
                session_id_value = self.get_value_from_template_dict("session_id")
                if session_id_value == "":
                    self.update_raw_params({"session_id": inputs["session"]}, overwrite=True)
            if self._is_chat_input() and (inputs or files):
                chat_input = {}
                if (
                    inputs
                    and isinstance(inputs, dict)
                    and "input_value" in inputs
                    and inputs.get("input_value") is not None
                ):
                    chat_input.update({"input_value": inputs.get(INPUT_FIELD_NAME, "")})
                if files:
                    chat_input.update({"files": files})

                self.update_raw_params(chat_input, overwrite=True)

            for step in self.steps:
                if step not in self.steps_ran:
                    await step(user_id=user_id, event_manager=event_manager, **kwargs)
                    self.steps_ran.append(step)

            self.finalize_build()

            flow_id = self.graph.flow_id
            if flow_id:
                outputs_dict = None
                if self.outputs_logs:
                    outputs_dict = {
                        k: v.model_dump() if hasattr(v, "model_dump") else v for k, v in self.outputs_logs.items()
                    }
                await self._log_transaction_async(
                    str(flow_id), source=self, target=None, status="success", outputs=outputs_dict
                )

        return await self.get_requester_result(requester)

    async def get_requester_result(self, requester: Vertex | None):
        """根据请求方节点返回可用结果。"""
        if requester is None:
            return self.built_object

        requester_edge = next((edge for edge in self.edges if edge.target_id == requester.id), None)
        return (
            None
            if requester_edge is None
            else await requester_edge.get_result_from_source(source=self, target=requester)
        )

    def add_edge(self, edge: CycleEdge) -> None:
        """向节点添加边引用。"""
        if edge not in self.edges:
            self.edges.append(edge)

    def __repr__(self) -> str:
        """调试表示。"""
        return f"Vertex(display_name={self.display_name}, id={self.id}, data={self.data})"

    def __eq__(self, /, other: object) -> bool:
        try:
            if not isinstance(other, Vertex):
                return False
            ids_are_equal = self.id == other.id
            data_are_equal = self.data == other.data
        except AttributeError:
            return False
        else:
            return ids_are_equal and data_are_equal

    def __hash__(self) -> int:
        """保持可哈希性以支持集合/字典。"""
        return id(self)

    def built_object_repr(self) -> str:
        """返回构建对象的简要文本表示。"""
        return "Built successfully ✨" if self.built_object is not None else "Failed to build 😵‍💫"

    def apply_on_outputs(self, func: Callable[[Any], Any]) -> None:
        """对输出映射应用函数。"""
        if not self.custom_component or not self.custom_component.outputs:
            return
        [func(output) for output in self.custom_component.get_outputs_map().values()]

    def raw_event_metrics(self, optional_fields: dict | None) -> dict:
        """生成用于 AGUI 事件的基础指标字段。"""
        if optional_fields is None:
            optional_fields = {}
        import time

        return {"timestamp": time.time(), **optional_fields}

    def before_callback_event(self, *args, **kwargs) -> StepStartedEvent:  # noqa: ARG002
        """生成 AGUI 开始事件。"""
        metrics = {}
        if hasattr(self, "raw_event_metrics"):
            metrics = self.raw_event_metrics({"component_id": self.id})

        return StepStartedEvent(step_name=self.display_name, raw_event={"langflow": metrics})

    def after_callback_event(self, result, *args, **kwargs) -> StepFinishedEvent:  # noqa: ARG002
        """生成 AGUI 结束事件。"""
        metrics = {}
        if hasattr(self, "raw_event_metrics"):
            metrics = self.raw_event_metrics({"component_id": self.id})
        return StepFinishedEvent(step_name=self.display_name, raw_event={"langflow": metrics})
