"""模块名称：通用组件运行时实现

本模块提供自定义组件的运行时核心实现，包括输入/输出映射、运行调度、工具模式、
前端节点构建、消息存储与事件分发等能力。

关键组件：
- `Component`：通用组件基类（运行时）
- `PlaceholderGraph`：无图环境下的兼容占位结构

设计背景：为自定义组件提供统一的执行模型与前端交互能力。
注意事项：大量方法依赖 `graph/vertex/event_manager`，在无图环境下会降级或抛错。
"""

from __future__ import annotations

import ast
import asyncio
import inspect
from collections.abc import AsyncIterator, Iterator
from copy import deepcopy
from textwrap import dedent
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple, get_type_hints
from uuid import UUID

import nanoid
import pandas as pd
import yaml
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ValidationError

from lfx.base.tools.constants import (
    TOOL_OUTPUT_DISPLAY_NAME,
    TOOL_OUTPUT_NAME,
    TOOLS_METADATA_INFO,
    TOOLS_METADATA_INPUT_NAME,
)
from lfx.custom.tree_visitor import RequiredInputsVisitor
from lfx.exceptions.component import StreamingError
from lfx.field_typing import Tool  # noqa: TC001

from lfx.helpers.custom import format_type
from lfx.memory import astore_message, aupdate_messages, delete_message
from lfx.schema.artifact import get_artifact_type, post_process_raw
from lfx.schema.data import Data
from lfx.schema.log import Log
from lfx.schema.message import ErrorMessage, Message
from lfx.schema.properties import Source
from lfx.serialization.serialization import serialize
from lfx.template.field.base import UNDEFINED, Input, Output
from lfx.template.frontend_node.custom_components import ComponentFrontendNode
from lfx.utils.async_helpers import run_until_complete
from lfx.utils.util import find_closest_match

from .custom_component import CustomComponent

if TYPE_CHECKING:
    from collections.abc import Callable

    from lfx.base.tools.component_tool import ComponentToolkit
    from lfx.events.event_manager import EventManager
    from lfx.graph.edge.schema import EdgeData
    from lfx.graph.vertex.base import Vertex
    from lfx.inputs.inputs import InputTypes
    from lfx.schema.dataframe import DataFrame
    from lfx.schema.log import LoggableType


_ComponentToolkit = None


def get_component_toolkit():
    global _ComponentToolkit  # noqa: PLW0603
    if _ComponentToolkit is None:
        from lfx.base.tools.component_tool import ComponentToolkit

        _ComponentToolkit = ComponentToolkit
    return _ComponentToolkit


BACKWARDS_COMPATIBLE_ATTRIBUTES = ["user_id", "vertex", "tracing_service"]
CONFIG_ATTRIBUTES = ["_display_name", "_description", "_icon", "_name", "_metadata"]


class PlaceholderGraph(NamedTuple):
    """组件图占位结构（兼容无图环境）。

    契约：提供 `flow_id/user_id/session_id/context/flow_name` 等最小字段；
    副作用无；失败语义：无。
    关键路径：1) 作为轻量图对象供组件读取 2) 提供空邻接信息。
    决策：仅保留必要字段
    问题：组件在无完整图时仍需运行
    方案：用 NamedTuple 提供最小上下文
    代价：缺少真实图的连边与状态
    重评：当无图执行不再需要时移除该占位结构

    字段说明：
    - `flow_id`：Flow 唯一标识
    - `user_id`：用户标识
    - `session_id`：会话标识
    - `context`：运行上下文
    - `flow_name`：Flow 名称
    """

    flow_id: str | None
    user_id: str | None
    session_id: str | None
    context: dict
    flow_name: str | None

    def get_vertex_neighbors(self, _vertex) -> dict:
        """返回空邻接关系。

        契约：输入 `_vertex`；输出空字典；副作用无；
        失败语义：无。
        关键路径：1) 直接返回 `{}`。
        决策：固定返回空邻接
        问题：占位图没有真实边信息
        方案：返回空字典以兼容调用
        代价：依赖邻接的逻辑可能失效
        重评：当占位图需要模拟边关系时扩展
        """
        return {}


class Component(CustomComponent):
    """通用组件运行时基类。

    契约：输入组件配置与输入/输出定义；输出组件执行结果与工件；
    副作用：更新图状态、写入日志与消息、触发事件；
    失败语义：配置/连接错误抛 `ValueError`，运行异常透传。
    关键路径：1) 初始化输入输出映射 2) 执行输出方法 3) 生成结果与工件。
    决策：输出与输入名不允许重名
    问题：重名会导致运行期歧义
    方案：初始化时检测并拒绝
    代价：限制输入/输出命名自由
    重评：当引入命名空间时可放宽限制
    """
    inputs: list[InputTypes] = []
    outputs: list[Output] = []
    selected_output: str | None = None
    code_class_base_inheritance: ClassVar[str] = "Component"

    def __init__(self, **kwargs) -> None:
        # 先初始化实例级属性
        if overlap := self._there_is_overlap_in_inputs_and_outputs():
            msg = f"Inputs and outputs have overlapping names: {overlap}"
            raise ValueError(msg)
        self._output_logs: dict[str, list[Log]] = {}
        self._current_output: str = ""
        self._metadata: dict = {}
        self._ctx: dict = {}
        self._code: str | None = None
        self._logs: list[Log] = []

        # 初始化组件级集合
        self._inputs: dict[str, InputTypes] = {}
        self._outputs_map: dict[str, Output] = {}
        self._results: dict[str, Any] = {}
        self._attributes: dict[str, Any] = {}
        self._edges: list[EdgeData] = []
        self._components: list[Component] = []
        self._event_manager: EventManager | None = None
        self._state_model = None
        self._telemetry_input_values: dict[str, Any] | None = None

        # 处理输入参数
        inputs = {}
        config = {}
        for key, value in kwargs.items():
            if key.startswith("_"):
                config[key] = value
            elif key in CONFIG_ATTRIBUTES:
                config[key[1:]] = value
            else:
                inputs[key] = value

        self._parameters = inputs or {}
        self.set_attributes(self._parameters)

        # 保存原始输入与配置用于调试
        self.__inputs = inputs
        self.__config = config or {}

        # 若未提供则生成唯一 ID
        if "_id" not in self.__config:
            self.__config |= {"_id": f"{self.__class__.__name__}-{nanoid.generate(size=5)}"}

        # 初始化父类
        super().__init__(**self.__config)

        # 后置初始化处理
        if hasattr(self, "_trace_type"):
            self.trace_type = self._trace_type
        if not hasattr(self, "trace_type"):
            self.trace_type = "chain"

        # 绑定输入与输出
        self.reset_all_output_values()
        if self.inputs is not None:
            self.map_inputs(self.inputs)
        self.map_outputs()

        # 最终初始化收尾
        self._set_output_types(list(self._outputs_map.values()))
        self.set_class_code()

    @classmethod
    def get_base_inputs(cls):
        if not hasattr(cls, "_base_inputs"):
            return []
        return cls._base_inputs

    @classmethod
    def get_base_outputs(cls):
        if not hasattr(cls, "_base_outputs"):
            return []
        return cls._base_outputs

    def get_results(self) -> dict[str, Any]:
        return self._results

    def get_artifacts(self) -> dict[str, Any]:
        return self._artifacts

    def get_event_manager(self) -> EventManager | None:
        return self._event_manager

    def get_undesrcore_inputs(self) -> dict[str, InputTypes]:
        return self._inputs

    def get_id(self) -> str:
        return self._id

    def set_id(self, id_: str) -> None:
        self._id = id_

    def get_edges(self) -> list[EdgeData]:
        return self._edges

    def get_components(self) -> list[Component]:
        return self._components

    def get_outputs_map(self) -> dict[str, Output]:
        return self._outputs_map

    def get_output_logs(self) -> dict[str, Any]:
        return self._output_logs

    def _build_source(self, id_: str | None, display_name: str | None, source: str | None) -> Source:
        source_dict = {}
        if id_:
            source_dict["id"] = id_
        if display_name:
            source_dict["display_name"] = display_name
        if source:
            # 处理 source 为模型对象的情况
            if hasattr(source, "model_name"):
                source_dict["source"] = source.model_name
            elif hasattr(source, "model"):
                source_dict["source"] = str(source.model)
            else:
                source_dict["source"] = str(source)
        return Source(**source_dict)

    def get_incoming_edge_by_target_param(self, target_param: str) -> str | None:
        """获取指向指定参数的入边源顶点 ID。

        契约：输入目标参数名；输出源顶点 ID 或 `None`；副作用无；
        失败语义：未构建图时抛 `ValueError`。
        关键路径：1) 校验 `_vertex` 2) 委托顶点查询入边。
        决策：委托给 Vertex 实现
        问题：组件不应直接维护边结构
        方案：复用顶点的边查询方法
        代价：依赖 Vertex 实现细节
        重评：当组件需要独立运行时引入本地缓存
        """
        if self._vertex is None:
            msg = "Vertex not found. Please build the graph first."
            raise ValueError(msg)
        return self._vertex.get_incoming_edge_by_target_param(target_param)

    @property
    def enabled_tools(self) -> list[str] | None:
        """返回启用的工具列表（可由子类重写）。

        契约：输入无；输出工具名/标签列表或 `None`；副作用无；
        失败语义：无。
        关键路径：1) 返回 `None` 表示全量启用。
        决策：默认全量启用
        问题：通用组件不应默认过滤工具
        方案：返回 `None` 交由下游处理
        代价：工具数量多时可能增加负载
        重评：当需要默认白名单时改为显式列表
        """
        # 默认返回 None 表示启用全部工具
        # 子类可通过重写进行过滤
        return None

    def _there_is_overlap_in_inputs_and_outputs(self) -> set[str]:
        """检查输入与输出名称是否重名。

        契约：输入无；输出重名集合；副作用无；
        失败语义：无。
        关键路径：1) 构建输入名集合 2) 构建输出名集合 3) 返回交集。
        决策：使用集合交集
        问题：名称冲突会导致连接歧义
        方案：初始化阶段提前检测
        代价：构造时多一次遍历
        重评：当命名空间隔离后可放宽
        """
        # 使用集合实现 O(1) 查找
        input_names = {input_.name for input_ in self.inputs if input_.name is not None}
        output_names = {output.name for output in self.outputs}

        # 返回交集作为重名集合
        return input_names & output_names

    def get_base_args(self):
        """获取组件初始化所需基础参数。

        契约：输入无；输出参数字典；副作用无；失败语义：无。
        关键路径：1) 读取 user/session/tracing 信息。
        决策：从当前 graph 与 tracing_service 读取
        问题：确保组件初始化具备最小上下文
        方案：集中在该方法统一返回
        代价：依赖 graph 已就绪
        重评：当无图运行时需要降级处理
        """
        return {
            "_user_id": self.user_id,
            "_session_id": self.graph.session_id,
            "_tracing_service": self.tracing_service,
        }

    @property
    def ctx(self):
        if not hasattr(self, "graph") or self.graph is None:
            msg = "Graph not found. Please build the graph first."
            raise ValueError(msg)
        return self.graph.context

    def add_to_ctx(self, key: str, value: Any, *, overwrite: bool = False) -> None:
        """向上下文添加键值对。

        契约：输入键与值；输出无；副作用：更新 graph.context；
        失败语义：图未构建或键冲突时抛 `ValueError`。
        关键路径：1) 校验 graph 2) 冲突检查 3) 更新 context。
        决策：默认不允许覆盖
        问题：防止不小心覆盖上下文关键字段
        方案：提供 `overwrite` 开关
        代价：需要调用方显式选择覆盖
        重评：当上下文支持版本化时放宽限制
        """
        if not hasattr(self, "graph") or self.graph is None:
            msg = "Graph not found. Please build the graph first."
            raise ValueError(msg)
        if key in self.graph.context and not overwrite:
            msg = f"Key {key} already exists in context. Set overwrite=True to overwrite."
            raise ValueError(msg)
        self.graph.context.update({key: value})

    def update_ctx(self, value_dict: dict[str, Any]) -> None:
        """批量更新上下文。

        契约：输入键值字典；输出无；副作用：更新 graph.context；
        失败语义：图未构建或入参非 dict 时抛异常。
        关键路径：1) 校验 graph 2) 校验类型 3) 更新 context。
        决策：仅接受 dict
        问题：避免错误类型导致上下文污染
        方案：类型检查后更新
        代价：输入限制更严格
        重评：当需要支持 Mapping 时放宽检查
        """
        if not hasattr(self, "graph") or self.graph is None:
            msg = "Graph not found. Please build the graph first."
            raise ValueError(msg)
        if not isinstance(value_dict, dict):
            msg = "Value dict must be a dictionary"
            raise TypeError(msg)

        self.graph.context.update(value_dict)

    def _pre_run_setup(self):
        pass

    def set_event_manager(self, event_manager: EventManager | None = None) -> None:
        self._event_manager = event_manager

    def reset_all_output_values(self) -> None:
        """将所有输出值重置为 `UNDEFINED`。

        契约：输入无；输出无；副作用：修改输出对象的 `value`；
        失败语义：无。
        关键路径：1) 遍历输出映射 2) 赋值为 `UNDEFINED`。
        决策：统一重置输出缓存
        问题：避免复用旧输出导致误结果
        方案：逐个清空输出值
        代价：可能丢失调试现场
        重评：当需要保留历史结果时引入快照机制
        """
        if isinstance(self._outputs_map, dict):
            for output in self._outputs_map.values():
                output.value = UNDEFINED

    def _build_state_model(self):
        """构建状态模型类（惰性）。

        契约：输入无；输出状态模型类；副作用：缓存 `_state_model`；
        失败语义：模型创建失败抛异常。
        关键路径：1) 生成模型名 2) 收集输出方法 3) 创建并缓存模型。
        决策：惰性构建并缓存
        问题：避免重复创建模型类
        方案：首次调用时创建并缓存
        代价：首次调用有额外开销
        重评：当模型变化频繁时改为每次重建
        """
        if self._state_model:
            return self._state_model
        name = self.name or self.__class__.__name__
        model_name = f"{name}StateModel"
        fields = {}
        for output in self._outputs_map.values():
            fields[output.name] = getattr(self, output.method)
        # 延迟导入以避免循环依赖
        from lfx.graph.state.model import create_state_model

        self._state_model = create_state_model(model_name=model_name, **fields)
        return self._state_model

    def get_state_model_instance_getter(self):
        state_model = self._build_state_model()

        def _instance_getter(_):
            return state_model()

        _instance_getter.__annotations__["return"] = state_model
        return _instance_getter

    def __deepcopy__(self, memo: dict) -> Component:
        if id(self) in memo:
            return memo[id(self)]
        kwargs = deepcopy(self.__config, memo)
        kwargs["inputs"] = deepcopy(self.__inputs, memo)
        new_component = type(self)(**kwargs)
        new_component._code = self._code
        new_component._outputs_map = self._outputs_map
        new_component._inputs = self._inputs
        new_component._edges = self._edges
        new_component._components = self._components
        new_component._parameters = self._parameters
        new_component._attributes = self._attributes
        new_component._output_logs = self._output_logs
        new_component._logs = self._logs  # type: ignore[attr-defined]
        memo[id(self)] = new_component
        return new_component

    def set_class_code(self) -> None:
        # 获取当前类所在模块的源码
        if self._code:
            return
        try:
            module = inspect.getmodule(self.__class__)
            if module is None:
                msg = "Could not find module for class"
                raise ValueError(msg)

            class_code = inspect.getsource(module)
            self._code = class_code
        except (OSError, TypeError) as e:
            msg = f"Could not find source code for {self.__class__.__name__}"
            raise ValueError(msg) from e

    def set(self, **kwargs):
        """连接组件或设置参数/属性。

        契约：输入连接/参数键值；输出 self；副作用：更新输入连接与属性；
        失败语义：输入名不存在时抛异常。
        关键路径：1) 遍历参数 2) 处理连接或参数设置 3) 返回 self。
        决策：统一通过 `_process_connection_or_parameters`
        问题：连接与参数设置需要统一入口
        方案：集中处理并复用逻辑
        代价：错误定位需要深入处理函数
        重评：当连接语义更复杂时拆分入口
        """
        for key, value in kwargs.items():
            self._process_connection_or_parameters(key, value)
        return self

    def list_inputs(self):
        """返回输入名称列表。

        契约：输入无；输出名称列表；副作用无；失败语义：无。
        关键路径：1) 遍历 `self.inputs` 2) 提取 `name`。
        决策：仅返回名称而非完整输入对象
        问题：前端/调试只需名称
        方案：列表推导
        代价：丢失输入元信息
        重评：当需要元信息时提供扩展方法
        """
        return [_input.name for _input in self.inputs]

    def list_outputs(self):
        """返回输出名称列表。

        契约：输入无；输出名称列表；副作用无；失败语义：无。
        关键路径：1) 遍历 `_outputs_map` 2) 提取输出名。
        决策：基于 `_outputs_map` 返回
        问题：确保返回实际可用输出
        方案：使用映射值
        代价：忽略原始 `self.outputs` 顺序
        重评：当需要保持顺序时返回 `self.outputs`
        """
        return [_output.name for _output in self._outputs_map.values()]

    async def run(self):
        """执行组件逻辑并返回结果。

        契约：输入无；输出执行结果；副作用：执行输出方法；
        失败语义：运行异常透传。
        关键路径：1) 调用 `_run`。
        决策：统一异步执行入口
        问题：组件可能包含异步逻辑
        方案：统一 await `_run`
        代价：同步调用需要包装
        重评：当支持同步执行路径时提供 `run_sync`
        """
        return await self._run()

    def set_vertex(self, vertex: Vertex) -> None:
        """设置组件关联的顶点。

        契约：输入 `Vertex`；输出无；副作用：更新 `_vertex`；
        失败语义：无。
        关键路径：1) 赋值 `_vertex`。
        决策：直接赋值，不做校验
        问题：保持设置逻辑简单
        方案：调用方保证有效性
        代价：无效顶点可能导致运行期错误
        重评：当需要校验时加入类型检查
        """
        self._vertex = vertex

    def get_input(self, name: str) -> Any:
        """按名称获取输入对象。

        契约：输入输入名；输出输入对象；副作用无；
        失败语义：不存在时抛 `ValueError`。
        关键路径：1) 查找 `_inputs` 2) 返回或抛错。
        决策：严格校验输入存在
        问题：避免静默返回错误输入
        方案：不存在直接抛错
        代价：调用方需处理异常
        重评：当需要容错时返回 `None`
        """
        if name in self._inputs:
            return self._inputs[name]
        msg = f"Input {name} not found in {self.__class__.__name__}"
        raise ValueError(msg)

    def get_output(self, name: str) -> Any:
        """按名称获取输出对象。

        契约：输入输出名；输出输出对象；副作用无；
        失败语义：不存在时抛 `ValueError`。
        关键路径：1) 查找 `_outputs_map` 2) 返回或抛错。
        决策：严格校验输出存在
        问题：避免错误输出映射
        方案：不存在直接抛错
        代价：调用方需处理异常
        重评：当需要容错时返回 `None`
        """
        if name in self._outputs_map:
            return self._outputs_map[name]
        msg = f"Output {name} not found in {self.__class__.__name__}"
        raise ValueError(msg)

    def set_on_output(self, name: str, **kwargs) -> None:
        output = self.get_output(name)
        for key, value in kwargs.items():
            if not hasattr(output, key):
                msg = f"Output {name} does not have a method {key}"
                raise ValueError(msg)
            setattr(output, key, value)

    def set_output_value(self, name: str, value: Any) -> None:
        if name in self._outputs_map:
            self._outputs_map[name].value = value
        else:
            msg = f"Output {name} not found in {self.__class__.__name__}"
            raise ValueError(msg)

    def map_outputs(self) -> None:
        """将输出列表映射到组件实例。

        契约：输入无（使用内部配置）；输出无；副作用：填充 `_outputs_map`；
        失败语义：输出名为空时抛 `ValueError`。
        关键路径：1) 选择顶点输出或类输出 2) 校验输出名 3) 深拷贝并映射。
        决策：顶点输出优先
        问题：前端可能覆盖默认输出定义
        方案：若有顶点输出则覆盖
        代价：前端配置不一致时可能导致差异
        重评：当需要合并策略时改为合并映射
        """
        # 使用顶点输出覆盖类定义输出（若存在）
        outputs = []
        if self._vertex and self._vertex.outputs:
            for output in self._vertex.outputs:
                try:
                    output_ = Output(**output)
                    outputs.append(output_)
                except ValidationError as e:
                    msg = f"Invalid output: {e}"
                    raise ValueError(msg) from e
        else:
            outputs = self.outputs
        for output in outputs:
            if output.name is None:
                msg = "Output name cannot be None."
                raise ValueError(msg)
            # 深拷贝避免修改原始组件定义，使实例可独立修改输出
            self._outputs_map[output.name] = deepcopy(output)

    def map_inputs(self, inputs: list[InputTypes]) -> None:
        """将输入列表映射到组件实例。

        契约：输入 `InputTypes` 列表；输出无；副作用：填充 `_inputs` 与遥测缓存；
        失败语义：输入名为空时抛 `ValueError`。
        关键路径：1) 深拷贝输入 2) 记录遥测值 3) 缓存输入值。
        决策：优先深拷贝输入对象
        问题：避免实例间共享输入状态
        方案：对可拷贝对象做 `deepcopy`
        代价：拷贝开销
        重评：当输入对象不可变时可直接引用
        """
        telemetry_values = {}

        for input_ in inputs:
            if input_.name is None:
                msg = self.build_component_error_message("Input name cannot be None")
                raise ValueError(msg)
            try:
                self._inputs[input_.name] = deepcopy(input_)
            except TypeError:
                self._inputs[input_.name] = input_

            # 在已有遍历中构建遥测数据（避免额外遍历）
            if self._should_track_input(input_):
                telemetry_values[input_.name] = serialize(input_.value)

        # 缓存以便后续 O(1) 读取
        self._telemetry_input_values = telemetry_values if telemetry_values else None

    def _should_track_input(self, input_obj: InputTypes) -> bool:
        """判断输入是否可纳入遥测。

        契约：输入 `InputTypes`；输出 bool；副作用无；
        失败语义：无。
        关键路径：1) 检查 `track_in_telemetry` 2) 排除敏感字段类型。
        决策：默认不追踪
        问题：保护敏感数据隐私
        方案：仅在显式开启且非敏感类型时追踪
        代价：可观测性降低
        重评：当需要更多可观测性时引入脱敏方案
        """
        from lfx.inputs.input_mixin import SENSITIVE_FIELD_TYPES

        # 尊重显式开关（默认 False 以保护隐私）
        if not getattr(input_obj, "track_in_telemetry", False):
            return False
        # 自动排除敏感字段类型
        return not (hasattr(input_obj, "field_type") and input_obj.field_type in SENSITIVE_FIELD_TYPES)

    def get_telemetry_input_values(self) -> dict[str, Any] | None:
        """获取缓存的遥测输入值。

        契约：输入无；输出遥测值字典或 `None`；副作用无；
        失败语义：无。
        关键路径：1) 返回缓存值。
        决策：返回原始缓存，不做过滤
        问题：保持与追踪时一致的值
        方案：直接返回缓存
        代价：包含描述性字符串或 None
        重评：当需要过滤时增加后处理
        """
        # 返回包含描述字段与 None 的原始缓存
        return self._telemetry_input_values if self._telemetry_input_values else None

    def validate(self, params: dict) -> None:
        """校验组件参数与输出配置。

        契约：输入参数字典；输出无；副作用：更新输入值；
        失败语义：校验失败抛 `ValueError`。
        关键路径：1) 校验输入 2) 校验输出。
        决策：先校验输入再校验输出
        问题：输入校验可能影响输出配置
        方案：顺序执行
        代价：校验开销
        重评：当需要并行校验时重构
        """
        self._validate_inputs(params)
        self._validate_outputs()

    async def run_and_validate_update_outputs(self, frontend_node: dict, field_name: str, field_value: Any):
        if inspect.iscoroutinefunction(self.update_outputs):
            frontend_node = await self.update_outputs(frontend_node, field_name, field_value)
        else:
            frontend_node = self.update_outputs(frontend_node, field_name, field_value)
        if field_name == "tool_mode" or frontend_node.get("tool_mode"):
            is_tool_mode = field_value or frontend_node.get("tool_mode")
            frontend_node["outputs"] = [self._build_tool_output()] if is_tool_mode else frontend_node["outputs"]
            if is_tool_mode:
                frontend_node.setdefault("template", {})
                frontend_node["tool_mode"] = True
                tools_metadata_input = await self._build_tools_metadata_input()
                frontend_node["template"][TOOLS_METADATA_INPUT_NAME] = tools_metadata_input.to_dict()
                self._append_tool_to_outputs_map()
            elif "template" in frontend_node:
                frontend_node["template"].pop(TOOLS_METADATA_INPUT_NAME, None)
        self.tools_metadata = frontend_node.get("template", {}).get(TOOLS_METADATA_INPUT_NAME, {}).get("value")
        return self._validate_frontend_node(frontend_node)

    def _validate_frontend_node(self, frontend_node: dict):
        # 校验所有输出均为 Output 或可转换为 Output
        for index, output in enumerate(frontend_node["outputs"]):
            if isinstance(output, dict):
                try:
                    output_ = Output(**output)
                    self._set_output_return_type(output_)
                    output_dict = output_.model_dump()
                except ValidationError as e:
                    msg = f"Invalid output: {e}"
                    raise ValueError(msg) from e
            elif isinstance(output, Output):
                # 需要序列化为字典
                self._set_output_return_type(output)
                output_dict = output.model_dump()
            else:
                msg = f"Invalid output type: {type(output)}"
                raise TypeError(msg)
            frontend_node["outputs"][index] = output_dict
        return frontend_node

    def update_outputs(self, frontend_node: dict, field_name: str, field_value: Any) -> dict:  # noqa: ARG002
        """根据字段变化更新输出（默认实现）。

        契约：输入前端节点与字段变更信息；输出更新后的节点；
        副作用无；失败语义：无。
        关键路径：1) 直接返回原节点。
        决策：默认不做变更
        问题：基础组件不需要动态调整输出
        方案：子类可重写实现联动
        代价：默认实现无更新逻辑
        重评：当需要统一联动规则时在基类实现
        """
        return frontend_node

    def _set_output_types(self, outputs: list[Output]) -> None:
        for output in outputs:
            self._set_output_return_type(output)

    def _set_output_return_type(self, output: Output) -> None:
        if output.method is None:
            msg = f"Output {output.name} does not have a method"
            raise ValueError(msg)
        return_types = self._get_method_return_type(output.method)
        output.add_types(return_types)

    def _set_output_required_inputs(self) -> None:
        for output in self.outputs:
            if not output.method:
                continue
            method = getattr(self, output.method, None)
            if not method or not callable(method):
                continue
            try:
                source_code = inspect.getsource(method)
                ast_tree = ast.parse(dedent(source_code))
            except Exception:  # noqa: BLE001
                ast_tree = ast.parse(dedent(self._code or ""))

            visitor = RequiredInputsVisitor(self._inputs)
            visitor.visit(ast_tree)
            output.required_inputs = sorted(visitor.required_inputs)

    def get_output_by_method(self, method: Callable):
        # `method` 为可调用对象，`output.method` 为方法名字符串
        # 需要找到方法名匹配的输出
        output = next((output for output in self._outputs_map.values() if output.method == method.__name__), None)
        if output is None:
            method_name = method.__name__ if hasattr(method, "__name__") else str(method)
            msg = f"Output with method {method_name} not found"
            raise ValueError(msg)
        return output

    def _inherits_from_component(self, method: Callable):
        # 判断方法是否来自继承 Component 的实例
        return hasattr(method, "__self__") and isinstance(method.__self__, Component)

    def _method_is_valid_output(self, method: Callable):
        # 判断方法是否属于 Component 实例并对应有效输出
        return (
            hasattr(method, "__self__")
            and isinstance(method.__self__, Component)
            and method.__self__.get_output_by_method(method)
        )

    def _build_error_string_from_matching_pairs(self, matching_pairs: list[tuple[Output, Input]]):
        text = ""
        for output, input_ in matching_pairs:
            text += f"{output.name}[{','.join(output.types)}]->{input_.name}[{','.join(input_.input_types or [])}]\n"
        return text

    def _find_matching_output_method(self, input_name: str, value: Component):
        """为指定输入匹配上游组件的输出方法。

        契约：输入输入名与上游组件；输出匹配的输出方法；
        副作用无；失败语义：无匹配或多匹配时抛 `ValueError`。
        关键路径：1) 获取上游输出 2) 按类型匹配 3) 返回唯一匹配方法。
        决策：要求唯一匹配
        问题：多输出匹配会造成连接歧义
        方案：多匹配直接报错
        代价：调用方需明确输出类型
        重评：当支持显式选择时允许多匹配
        """
        # 获取上游组件输出
        outputs = value._outputs_map.values()
        # 收集匹配的输出-输入对
        matching_pairs = []
        # 获取当前组件的输入对象
        input_ = self._inputs[input_name]
        # 按类型匹配输出
        matching_pairs = [
            (output, input_)
            for output in outputs
            for output_type in output.types
            # 输出类型需在输入允许列表中
            if input_.input_types and output_type in input_.input_types
        ]
        # 多匹配时抛错（避免歧义）
        if len(matching_pairs) > 1:
            matching_pairs_str = self._build_error_string_from_matching_pairs(matching_pairs)
            msg = self.build_component_error_message(
                f"There are multiple outputs from {value.display_name} that can connect to inputs: {matching_pairs_str}"
            )
            raise ValueError(msg)
        # 无匹配时抛错
        if not matching_pairs:
            msg = self.build_input_error_message(input_name, f"No matching output from {value.display_name} found")
            raise ValueError(msg)
        # 取唯一匹配项
        output, input_ = matching_pairs[0]
        # 确保输出方法名为字符串
        if not isinstance(output.method, str):
            msg = self.build_component_error_message(
                f"Method {output.method} is not a valid output of {value.display_name}"
            )
            raise TypeError(msg)
        return getattr(value, output.method)

    def _process_connection_or_parameter(self, key, value) -> None:
        # `Loop` 组件的特殊处理：检查是否设置回环输出
        if self._is_loop_connection(key, value):
            self._process_loop_connection(key, value)
            return

        input_ = self._get_or_create_input(key)
        # 检查是否为 Component 方法或组件实例
        if isinstance(value, Component):
            # 在上游组件中寻找与当前输入匹配的输出（多匹配则报错）
            value = self._find_matching_output_method(key, value)
        if callable(value) and self._inherits_from_component(value):
            try:
                self._method_is_valid_output(value)
            except ValueError as e:
                msg = f"Method {value.__name__} is not a valid output of {value.__self__.__class__.__name__}"
                raise ValueError(msg) from e
            self._connect_to_component(key, value, input_)
        else:
            self._set_parameter_or_attribute(key, value)

    def _is_loop_connection(self, key: str, value) -> bool:
        """判断是否为回环连接。

        契约：输入键与值；输出 bool；副作用无；
        失败语义：无。
        关键路径：1) 检查输出名 2) 检查 `allows_loop` 3) 检查值类型。
        决策：仅允许连接到允许回环的输出
        问题：回环连接需要显式授权
        方案：通过 `allows_loop` 标志控制
        代价：配置复杂度增加
        重评：当回环策略统一时可简化
        """
        # 输出名必须匹配并允许回环
        if key not in self._outputs_map:
            return False

        output = self._outputs_map[key]
        if not getattr(output, "allows_loop", False):
            return False

        # 值需为 Component 的可调用方法
        return callable(value) and self._inherits_from_component(value)

    def _process_loop_connection(self, key: str, value) -> None:
        """处理回环反馈连接。

        契约：输入输出名与上游方法；输出无；副作用：追加回环边；
        失败语义：方法无效时抛 `ValueError`。
        关键路径：1) 校验输出方法 2) 获取源输出 3) 添加回环边。
        决策：将回环边指向输出而非输入
        问题：Loop 需要连接到自身输出以实现反馈
        方案：构造特殊边结构
        代价：边结构与普通边不同
        重评：当引入通用回环机制时统一结构
        """
        try:
            self._method_is_valid_output(value)
        except ValueError as e:
            msg = f"Method {value.__name__} is not a valid output of {value.__self__.__class__.__name__}"
            raise ValueError(msg) from e

        source_component = value.__self__
        self._components.append(source_component)
        source_output = source_component.get_output_by_method(value)
        target_output = self._outputs_map[key]

        # 创建回环反馈边
        self._add_loop_edge(source_component, source_output, target_output)

    def _add_loop_edge(self, source_component, source_output, target_output) -> None:
        """添加回环反馈边（指向输出而非输入）。

        契约：输入源组件/输出与目标输出；输出无；副作用：追加边到 `_edges`；
        失败语义：无。
        关键路径：1) 构造边数据 2) 追加到列表。
        决策：使用特殊 `targetHandle` 结构
        问题：回环连接不对应输入字段
        方案：在 `targetHandle` 中存放输出信息
        代价：与普通边结构不同
        重评：当引入统一边类型时合并结构
        """
        self._edges.append(
            {
                "source": source_component._id,
                "target": self._id,
                "data": {
                    "sourceHandle": {
                        "dataType": source_component.name or source_component.__class__.__name__,
                        "id": source_component._id,
                        "name": source_output.name,
                        "output_types": source_output.types,
                    },
                    "targetHandle": {
                        # 回环边结构：目标指向输出而非输入
                        "dataType": self.name or self.__class__.__name__,
                        "id": self._id,
                        "name": target_output.name,
                        "output_types": target_output.types,
                    },
                },
            }
        )

    def _process_connection_or_parameters(self, key, value) -> None:
        # 若 value 是组件列表，则逐个处理（排除基础类型列表）
        if isinstance(value, list) and not any(
            isinstance(val, str | int | float | bool | type(None) | Message | Data | StructuredTool) for val in value
        ):
            for val in value:
                self._process_connection_or_parameter(key, val)
        else:
            self._process_connection_or_parameter(key, value)

    def _get_or_create_input(self, key):
        try:
            return self._inputs[key]
        except KeyError:
            input_ = self._get_fallback_input(name=key, display_name=key)
            self._inputs[key] = input_
            self.inputs.append(input_)
            return input_

    def _connect_to_component(self, key, value, input_) -> None:
        component = value.__self__
        self._components.append(component)
        output = component.get_output_by_method(value)
        self._add_edge(component, key, output, input_)

    def _add_edge(self, component, key, output, input_) -> None:
        self._edges.append(
            {
                "source": component._id,
                "target": self._id,
                "data": {
                    "sourceHandle": {
                        "dataType": component.name or component.__class__.__name__,
                        "id": component._id,
                        "name": output.name,
                        "output_types": output.types,
                    },
                    "targetHandle": {
                        "fieldName": key,
                        "id": self._id,
                        "inputTypes": input_.input_types,
                        "type": input_.field_type,
                    },
                },
            }
        )

    def _set_parameter_or_attribute(self, key, value) -> None:
        if isinstance(value, Component):
            methods = ", ".join([f"'{output.method}'" for output in value.outputs])
            msg = f"You set {value.display_name} as value for `{key}`. You should pass one of the following: {methods}"
            raise TypeError(msg)
        self.set_input_value(key, value)
        self._parameters[key] = value
        self._attributes[key] = value

    def __call__(self, **kwargs):
        self.set(**kwargs)

        return run_until_complete(self.run())

    async def _run(self):
        # 解析可调用输入（支持协程或同步函数）
        for key, _input in self._inputs.items():
            if asyncio.iscoroutinefunction(_input.value):
                self._inputs[key].value = await _input.value()
            elif callable(_input.value):
                self._inputs[key].value = await asyncio.to_thread(_input.value)

        self.set_attributes({})

        return await self.build_results()

    def __getattr__(self, name: str) -> Any:
        if "_attributes" in self.__dict__ and name in self.__dict__["_attributes"]:
            # 非输入/输出的自定义属性字典（可能包含回环输入数据）
            return self.__dict__["_attributes"][name]
        if "_inputs" in self.__dict__ and name in self.__dict__["_inputs"]:
            return self.__dict__["_inputs"][name].value
        if "_outputs_map" in self.__dict__ and name in self.__dict__["_outputs_map"]:
            return self.__dict__["_outputs_map"][name]
        if name in BACKWARDS_COMPATIBLE_ATTRIBUTES:
            return self.__dict__[f"_{name}"]
        if name.startswith("_") and name[1:] in BACKWARDS_COMPATIBLE_ATTRIBUTES:
            return self.__dict__[name]
        if name == "graph":
            # 走到这里说明正常路径会抛错，改用占位图
            session_id = self._session_id if hasattr(self, "_session_id") else None
            user_id = self._user_id if hasattr(self, "_user_id") else None
            flow_name = self._flow_name if hasattr(self, "_flow_name") else None
            flow_id = self._flow_id if hasattr(self, "_flow_id") else None
            return PlaceholderGraph(
                flow_id=flow_id, user_id=str(user_id), session_id=session_id, context={}, flow_name=flow_name
            )
        msg = f"Attribute {name} not found in {self.__class__.__name__}"
        raise AttributeError(msg)

    def set_input_value(self, name: str, value: Any) -> None:
        if name in self._inputs:
            input_value = self._inputs[name].value
            if isinstance(input_value, Component):
                methods = ", ".join([f"'{output.method}'" for output in input_value.outputs])
                msg = self.build_input_error_message(
                    name,
                    f"You set {input_value.display_name} as value. You should pass one of the following: {methods}",
                )
                raise ValueError(msg)
            if callable(input_value) and hasattr(input_value, "__self__"):
                msg = self.build_input_error_message(
                    name, f"Input is connected to {input_value.__self__.display_name}.{input_value.__name__}"
                )
                raise ValueError(msg)
            try:
                self._inputs[name].value = value
            except Exception as e:
                msg = f"Error setting input value for {name}: {e}"
                raise ValueError(msg) from e
            if hasattr(self._inputs[name], "load_from_db"):
                self._inputs[name].load_from_db = False
        else:
            msg = self.build_component_error_message(f"Input {name} not found")
            raise ValueError(msg)

    def _validate_outputs(self) -> None:
        # 若不满足规则则抛错
        if self.selected_output is not None and self.selected_output not in self._outputs_map:
            output_names = ", ".join(list(self._outputs_map.keys()))
            msg = f"selected_output '{self.selected_output}' is not valid. Must be one of: {output_names}"
            raise ValueError(msg)

    def _map_parameters_on_frontend_node(self, frontend_node: ComponentFrontendNode) -> None:
        for name, value in self._parameters.items():
            frontend_node.set_field_value_in_template(name, value)

    def _map_parameters_on_template(self, template: dict) -> None:
        for name, value in self._parameters.items():
            try:
                template[name]["value"] = value
            except KeyError as e:
                close_match = find_closest_match(name, list(template.keys()))
                if close_match:
                    msg = f"Parameter '{name}' not found in {self.__class__.__name__}. Did you mean '{close_match}'?"
                    raise ValueError(msg) from e
                msg = f"Parameter {name} not found in {self.__class__.__name__}. "
                raise ValueError(msg) from e

    def _get_method_return_type(self, method_name: str) -> list[str]:
        method = getattr(self, method_name)
        return_type = get_type_hints(method).get("return")
        if return_type is None:
            return []
        extracted_return_types = self._extract_return_type(return_type)
        return [format_type(extracted_return_type) for extracted_return_type in extracted_return_types]

    def _update_template(self, frontend_node: dict):
        return frontend_node

    def to_frontend_node(self):
        # 兼容旧版前端节点结构的历史包袱，后续可重构
        field_config = self.get_template_config(self)
        frontend_node = ComponentFrontendNode.from_inputs(**field_config)
        # 可选：按需关闭从数据库加载（示例保留）
        self._map_parameters_on_frontend_node(frontend_node)

        frontend_node_dict = frontend_node.to_dict(keep_name=False)
        frontend_node_dict = self._update_template(frontend_node_dict)
        self._map_parameters_on_template(frontend_node_dict["template"])

        frontend_node = ComponentFrontendNode.from_dict(frontend_node_dict)
        if not self._code:
            self.set_class_code()
        code_field = Input(
            dynamic=True,
            required=True,
            placeholder="",
            multiline=True,
            value=self._code,
            password=False,
            name="code",
            advanced=True,
            field_type="code",
            is_list=False,
        )
        frontend_node.template.add_field(code_field)

        for output in frontend_node.outputs:
            if output.types:
                continue
            return_types = self._get_method_return_type(output.method)
            output.add_types(return_types)

        frontend_node.validate_component()
        frontend_node.set_base_classes_from_outputs()

        # 获取节点字典并附加 selected_output（若存在）
        node_dict = frontend_node.to_dict(keep_name=False)
        if self.selected_output is not None:
            node_dict["selected_output"] = self.selected_output

        return {
            "data": {
                "node": node_dict,
                "type": self.name or self.__class__.__name__,
                "id": self._id,
            },
            "id": self._id,
        }

    def _validate_inputs(self, params: dict) -> None:
        # `params` 的 key 对应 `Input.name`
        """校验并写入输入值。

        契约：输入参数字典；输出无；副作用：更新输入值并回写 params；
        失败语义：无（未知参数跳过）。
        关键路径：1) 遍历参数 2) 更新对应输入值 3) 回写校验后的值。
        决策：仅处理已定义输入
        问题：避免未知参数污染输入
        方案：不存在则跳过
        代价：静默忽略未知参数
        重评：当需要严格校验时改为报错
        """
        for key, value in params.copy().items():
            if key not in self._inputs:
                continue
            input_ = self._inputs[key]
            # `BaseInputMixin` 启用了 `validate_assignment=True`

            input_.value = value
            params[input_.name] = input_.value

    def set_attributes(self, params: dict) -> None:
        """设置组件属性并防止保留字段冲突。

        契约：输入参数字典；输出无；副作用：更新 `_attributes`；
        失败语义：与保留属性冲突时抛 `ValueError`。
        关键路径：1) 校验输入 2) 构建属性字典 3) 写入 `_attributes`。
        决策：保留字段冲突直接报错
        问题：避免用户参数覆盖内部字段
        方案：检查 `__dict__` 与 `_attributes`
        代价：限制可用字段名
        重评：当引入命名空间时可放宽
        """
        self._validate_inputs(params)
        attributes = {}
        for key, value in params.items():
            if key in self.__dict__ and key not in self._attributes and value != getattr(self, key):
                msg = (
                    f"{self.__class__.__name__} defines an input parameter named '{key}' "
                    f"that is a reserved word and cannot be used."
                )
                raise ValueError(msg)
            attributes[key] = value
        for key, input_obj in self._inputs.items():
            if key not in attributes and key not in self._attributes:
                attributes[key] = input_obj.value or None

        self._attributes.update(attributes)

    def _set_outputs(self, outputs: list[dict]) -> None:
        self.outputs = [Output(**output) for output in outputs]
        for output in self.outputs:
            setattr(self, output.name, output)
            self._outputs_map[output.name] = output

    def get_trace_as_inputs(self):
        predefined_inputs = {
            input_.name: input_.value
            for input_ in self.inputs
            if hasattr(input_, "trace_as_input") and input_.trace_as_input
        }
        # 运行期输入
        runtime_inputs = {name: input_.value for name, input_ in self._inputs.items() if hasattr(input_, "value")}
        return {**predefined_inputs, **runtime_inputs}

    def get_trace_as_metadata(self):
        return {
            input_.name: input_.value
            for input_ in self.inputs
            if hasattr(input_, "trace_as_metadata") and input_.trace_as_metadata
        }

    async def _build_with_tracing(self):
        inputs = self.get_trace_as_inputs()
        metadata = self.get_trace_as_metadata()
        async with self.tracing_service.trace_component(self, self.trace_name, inputs, metadata):
            results, artifacts = await self._build_results()
            self.tracing_service.set_outputs(self.trace_name, results)

        return results, artifacts

    async def _build_without_tracing(self):
        return await self._build_results()

    async def build_results(self):
        """构建组件结果。

        契约：输入无；输出 `(results, artifacts)`；副作用：发送错误消息、更新状态；
        失败语义：流式错误包装为 `StreamingError`，其他异常透传。
        关键路径：1) 选择带/不带 tracing 的执行 2) 捕获异常并上报。
        决策：优先走 tracing 流程
        问题：需要统一可观测性
        方案：根据 tracing_service 分支执行
        代价：有 tracing 时增加开销
        重评：当不需要 tracing 时提供显式开关
        """
        if hasattr(self, "graph"):
            session_id = self.graph.session_id
        elif hasattr(self, "_session_id"):
            session_id = self._session_id
        else:
            session_id = None
        try:
            if self.tracing_service:
                return await self._build_with_tracing()
            return await self._build_without_tracing()
        except StreamingError as e:
            await self.send_error(
                exception=e.cause,
                session_id=session_id,
                trace_name=getattr(self, "trace_name", None),
                source=e.source,
            )
            raise e.cause  # noqa: B904
        except Exception as e:
            await self.send_error(
                exception=e,
                session_id=session_id,
                source=Source(id=self._id, display_name=self.display_name, source=self.display_name),
                trace_name=getattr(self, "trace_name", None),
            )
            raise

    async def _build_results(self) -> tuple[dict, dict]:
        results, artifacts = {}, {}

        self._pre_run_setup_if_needed()
        self._handle_tool_mode()

        for output in self._get_outputs_to_process():
            self._current_output = output.name
            result = await self._get_output_result(output)
            results[output.name] = result
            artifacts[output.name] = self._build_artifact(result)
            self._log_output(output)

        self._finalize_results(results, artifacts)
        return results, artifacts

    def _pre_run_setup_if_needed(self):
        if hasattr(self, "_pre_run_setup"):
            self._pre_run_setup()

    def _handle_tool_mode(self):
        if (
            hasattr(self, "outputs") and any(getattr(_input, "tool_mode", False) for _input in self.inputs)
        ) or self.add_tool_output:
            self._append_tool_to_outputs_map()

    def _should_process_output(self, output):
        """判断输出是否需要处理。

        契约：输入输出对象；输出 bool；副作用无；
        失败语义：无。
        关键路径：1) 检查是否有出边 2) 判断输出名是否在出边列表。
        决策：无出边时全部输出
        问题：避免计算未连接的输出
        方案：仅处理被连接的输出
        代价：未连接输出不会计算
        重评：当需要全量计算时移除过滤
        """
        if not self._vertex or not self._vertex.outgoing_edges:
            return True
        return output.name in self._vertex.edges_source_names

    def _get_outputs_to_process(self):
        """按顺序获取需要处理的输出列表。

        契约：输入无；输出输出列表；副作用无；
        失败语义：无（缺失输出时使用 deepcopy 回退）。
        关键路径：1) 按 `self.outputs` 顺序过滤 2) 补齐 `_outputs_map` 中未包含项。
        决策：保持 `self.outputs` 的顺序优先
        问题：前端输出顺序需要稳定
        方案：先按 `self.outputs`，再补齐剩余输出
        代价：可能出现重复处理的风险（通过 processed_names 避免）
        重评：当输出顺序不重要时简化逻辑
        """
        result = []
        processed_names = set()

        # 先按 self.outputs 顺序处理
        for output in self.outputs:
            output_obj = self._outputs_map.get(output.name, deepcopy(output))
            if self._should_process_output(output_obj):
                result.append(output_obj)
                processed_names.add(output_obj.name)

        # 再处理 _outputs_map 中的剩余输出
        for name, output_obj in self._outputs_map.items():
            if name not in processed_names and self._should_process_output(output_obj):
                result.append(output_obj)

        return result

    async def _get_output_result(self, output):
        """计算并返回单个输出结果。

        契约：输入输出对象；输出结果；副作用：可能更新缓存与输出值；
        失败语义：输出方法缺失抛 `ValueError`，调用失败抛 `TypeError`。
        关键路径：1) 命中缓存直接返回 2) 调用输出方法 3) 应用选项并缓存。
        决策：缓存命中时不重复计算
        问题：减少重复计算开销
        方案：检查 `output.cache` 与 `value`
        代价：缓存可能导致旧值
        重评：当需要强一致时忽略缓存
        """
        if output.cache and output.value != UNDEFINED:
            return output.value

        if output.method is None:
            msg = f'Output "{output.name}" does not have a method defined.'
            raise ValueError(msg)

        method = getattr(self, output.method)
        try:
            result = await method() if inspect.iscoroutinefunction(method) else await asyncio.to_thread(method)
        except TypeError as e:
            msg = f'Error running method "{output.method}": {e}'
            raise TypeError(msg) from e

        if (
            self._vertex is not None
            and isinstance(result, Message)
            and result.flow_id is None
            and self._vertex.graph.flow_id is not None
        ):
            result.set_flow_id(self._vertex.graph.flow_id)
        result = output.apply_options(result)
        output.value = result

        return result

    async def resolve_output(self, output_name: str) -> Any:
        """按名称解析输出值。

        契约：输入输出名；输出结果；副作用：可能触发计算；
        失败语义：输出不存在时抛 `KeyError`。
        关键路径：1) 获取输出对象 2) 命中缓存或计算结果。
        决策：缓存命中直接返回
        问题：避免重复计算
        方案：检查 `output.cache` 与 `value`
        代价：返回旧值的可能
        重评：当需要实时计算时忽略缓存
        """
        output = self._outputs_map.get(output_name)
        if output is None:
            msg = (
                f"Sorry, an output named '{output_name}' could not be found. "
                "Please ensure that the output is correctly configured and try again."
            )
            raise KeyError(msg)
        if output.cache and output.value != UNDEFINED:
            return output.value
        return await self._get_output_result(output)

    def _build_artifact(self, result):
        """构建工件字典（repr/raw/type）。

        契约：输入结果对象；输出工件字典；副作用无；
        失败语义：无。
        关键路径：1) 生成展示文本 2) 提取原始结果 3) 判定类型并后处理。
        决策：使用 `custom_repr` 作为首选展示
        问题：需要同时提供可读与可追踪数据
        方案：组合 `repr/raw/type`
        代价：字符串化可能丢失结构
        重评：当需要结构化展示时扩展工件字段
        """
        custom_repr = self.custom_repr()
        if custom_repr is None and isinstance(result, dict | Data | str):
            custom_repr = result
        if not isinstance(custom_repr, str):
            custom_repr = str(custom_repr)

        raw = self._process_raw_result(result)
        artifact_type = get_artifact_type(self.status or raw, result)
        raw, artifact_type = post_process_raw(raw, artifact_type)
        return {"repr": custom_repr, "raw": raw, "type": artifact_type}

    def _process_raw_result(self, result):
        return self.extract_data(result)

    def extract_data(self, result):
        """从结果中提取数据并更新状态。

        契约：输入结果对象；输出可序列化数据；副作用：更新 `self.status`；
        失败语义：无（回退到原始结果）。
        关键路径：1) 处理 Message 2) 处理 data/model_dump 3) 回退原始值。
        决策：Message 优先提取文本
        问题：统一不同结果类型的输出形态
        方案：按类型分支处理
        代价：可能丢失部分字段
        重评：当需要完整结构时返回更丰富数据
        """
        if isinstance(result, Message):
            self.status = result.get_text()
            return (
                self.status if self.status is not None else "No text available"
            )  # 若缺少文本则提供默认提示
        if hasattr(result, "data"):
            return result.data
        if hasattr(result, "model_dump"):
            return result.model_dump()
        if isinstance(result, Data | dict | str):
            return result.data if isinstance(result, Data) else result

        if self.status:
            return self.status
        return result

    def _log_output(self, output):
        self._output_logs[output.name] = self._logs
        self._logs = []
        self._current_output = ""

    def _finalize_results(self, results, artifacts):
        self._artifacts = artifacts
        self._results = results
        if self.tracing_service:
            self.tracing_service.set_outputs(self.trace_name, results)

    def custom_repr(self):
        if self.repr_value == "":
            self.repr_value = self.status
        if isinstance(self.repr_value, dict):
            return yaml.dump(self.repr_value)
        if isinstance(self.repr_value, str):
            return self.repr_value
        if isinstance(self.repr_value, BaseModel) and not isinstance(self.repr_value, Data):
            return str(self.repr_value)
        return self.repr_value

    def build_inputs(self):
        """构建输入配置字典。

        契约：输入无；输出输入配置字典；副作用：更新 `self.inputs`；
        失败语义：无（无输入时返回空字典）。
        关键路径：1) 获取模板输入 2) 生成字典结构。
        决策：输出以 Input.name 为键
        问题：前端需要 name->配置映射
        方案：使用 `model_dump` 生成字段配置
        代价：丢失对象方法
        重评：当需要完整对象时返回 Input 列表
        """
        # 类似 build_config，但返回 name->配置 的字典
        self.inputs = self.template_config.get("inputs", [])
        if not self.inputs:
            return {}
        return {_input.name: _input.model_dump(by_alias=True, exclude_none=True) for _input in self.inputs}

    def _get_field_order(self):
        try:
            inputs = self.template_config["inputs"]
            return [field.name for field in inputs]
        except KeyError:
            return []

    def build(self, **kwargs) -> None:
        self.set_attributes(kwargs)

    def _get_fallback_input(self, **kwargs):
        return Input(**kwargs)

    async def to_toolkit(self) -> list[Tool]:
        """将组件转换为工具列表。

        契约：输入无；输出 `list[Tool]`；副作用：可能更新工具元数据；
        失败语义：工具获取失败抛异常。
        关键路径：1) 获取工具 2) 按元数据过滤 3) 更新元数据。
        决策：兼容同步/异步 `_get_tools`
        问题：不同组件实现方式不一致
        方案：运行时判断并调用
        代价：多一次分支判断
        重评：当 `_get_tools` 统一为异步后移除判断
        """
        # 从子类实现获取工具（兼容同步/异步）
        if asyncio.iscoroutinefunction(self._get_tools):
            tools = await self._get_tools()
        else:
            tools = self._get_tools()

        if hasattr(self, TOOLS_METADATA_INPUT_NAME):
            tools = self._filter_tools_by_status(tools=tools, metadata=self.tools_metadata)
            return self._update_tools_with_metadata(tools=tools, metadata=self.tools_metadata)

        # 无元数据时根据 enabled_tools 过滤
        return self._filter_tools_by_status(tools=tools, metadata=None)

    async def _get_tools(self) -> list[Tool]:
        """获取组件工具列表（默认实现）。

        契约：输入无；输出 `list[Tool]`；副作用：无；
        失败语义：工具构建失败抛异常。
        关键路径：1) 获取 ComponentToolkit 2) 构建工具列表。
        决策：默认使用 ComponentToolkit
        问题：避免重复实现工具构建逻辑
        方案：委托 toolkit
        代价：可定制性受限
        重评：当需要定制时由子类重写
        """
        component_toolkit: type[ComponentToolkit] = get_component_toolkit()
        return component_toolkit(component=self).get_tools(callbacks=self.get_langchain_callbacks())

    def _extract_tools_tags(self, tools_metadata: list[dict]) -> list[str]:
        """提取工具元数据中的首个标签。

        契约：输入工具元数据列表；输出标签列表；副作用无；失败语义：无。
        关键路径：1) 遍历元数据 2) 提取 tags[0]。
        决策：仅取首个标签
        问题：状态变更只需一个稳定标识
        方案：使用首标签作为比较基准
        代价：忽略后续标签
        重评：当需要多标签比较时扩展逻辑
        """
        return [tool["tags"][0] for tool in tools_metadata if tool["tags"]]

    def _update_tools_with_metadata(self, tools: list[Tool], metadata: DataFrame | None) -> list[Tool]:
        """用元数据更新工具信息。

        契约：输入工具列表与元数据；输出更新后的工具列表；副作用无；
        失败语义：无。
        关键路径：1) 获取 toolkit 2) 调用更新方法。
        决策：复用 ComponentToolkit 的更新能力
        问题：保持工具元数据与 UI 一致
        方案：委托 toolkit 更新
        代价：依赖 toolkit 实现
        重评：当元数据结构稳定后可内联实现
        """
        component_toolkit: type[ComponentToolkit] = get_component_toolkit()
        return component_toolkit(component=self, metadata=metadata).update_tools_metadata(tools=tools)

    def check_for_tool_tag_change(self, old_tags: list[str], new_tags: list[str]) -> bool:
        # 先检查长度，长度不同必然变化
        if len(old_tags) != len(new_tags):
            return True
        # 使用集合比较降低平均复杂度
        return set(old_tags) != set(new_tags)

    def _filter_tools_by_status(self, tools: list[Tool], metadata: pd.DataFrame | None) -> list[Tool]:
        """根据元数据状态过滤工具。

        契约：输入工具列表与元数据；输出过滤后的工具列表；
        副作用无；失败语义：无。
        关键路径：1) 规范化元数据 2) 根据状态过滤。
        决策：元数据缺失时使用 enabled_tools
        问题：需要兼容无元数据场景
        方案：无元数据时按 enabled_tools 过滤
        代价：状态信息可能缺失
        重评：当元数据必填时移除回退
        """
        # 若元数据为 DataFrame，转换为 list[dict]
        metadata_dict = None  # 初始化为 None，避免空 dict 的 lint 问题
        if isinstance(metadata, pd.DataFrame):
            metadata_dict = metadata.to_dict(orient="records")

        # 元数据为空时使用 enabled_tools
        if not metadata_dict:
            enabled = self.enabled_tools
            return (
                tools
                if enabled is None
                else [
                    tool for tool in tools if any(enabled_name in [tool.name, *tool.tags] for enabled_name in enabled)
                ]
            )

        # 确保元数据为列表结构
        if not isinstance(metadata_dict, list):
            return tools

        # 构建工具名到状态的映射
        tool_status = {item["name"]: item.get("status", True) for item in metadata_dict}
        return [tool for tool in tools if tool_status.get(tool.name, True)]

    def _build_tool_data(self, tool: Tool) -> dict:
        if tool.metadata is None:
            tool.metadata = {}
        return {
            "name": tool.name,
            "description": tool.description,
            "tags": tool.tags if hasattr(tool, "tags") and tool.tags else [tool.name],
            "status": True,  # 默认启用
            "display_name": tool.metadata.get("display_name", tool.name),
            "display_description": tool.metadata.get("display_description", tool.description),
            "readonly": tool.metadata.get("readonly", False),
            "args": tool.args,
            # "args_schema": tool.args_schema,
        }

    async def _build_tools_metadata_input(self):
        try:
            from lfx.inputs.inputs import ToolsInput
        except ImportError as e:
            msg = "Failed to import ToolsInput from lfx.inputs.inputs"
            raise ImportError(msg) from e
        placeholder = None
        tools = []
        try:
            # 兼容同步/异步 _get_tools
            # 待办：当 _get_tools 全量异步后可移除判断
            if asyncio.iscoroutinefunction(self._get_tools):
                tools = await self._get_tools()
            else:
                tools = self._get_tools()

            placeholder = "Loading actions..." if len(tools) == 0 else ""
        except (TimeoutError, asyncio.TimeoutError):
            placeholder = "Timeout loading actions"
        except (ConnectionError, OSError, ValueError):
            placeholder = "Error loading actions"
        # 始终使用最新工具数据
        tool_data = [self._build_tool_data(tool) for tool in tools]
        if hasattr(self, TOOLS_METADATA_INPUT_NAME):
            old_tags = self._extract_tools_tags(self.tools_metadata)
            new_tags = self._extract_tools_tags(tool_data)
            if self.check_for_tool_tag_change(old_tags, new_tags):
                # 若设置了 enabled_tools，则根据其更新状态
                enabled = self.enabled_tools
                if enabled is not None:
                    for item in tool_data:
                        item["status"] = any(enabled_name in [item["name"], *item["tags"]] for enabled_name in enabled)
                self.tools_metadata = tool_data
            else:
                # 保留已有状态
                existing_status = {item["name"]: item.get("status", True) for item in self.tools_metadata}
                for item in tool_data:
                    item["status"] = existing_status.get(item["name"], True)
                tool_data = self.tools_metadata
        else:
            # 若设置了 enabled_tools，则根据其更新状态
            enabled = self.enabled_tools
            if enabled is not None:
                for item in tool_data:
                    item["status"] = any(enabled_name in [item["name"], *item["tags"]] for enabled_name in enabled)
            self.tools_metadata = tool_data

        return ToolsInput(
            name=TOOLS_METADATA_INPUT_NAME,
            placeholder=placeholder,
            display_name="Actions",
            info=TOOLS_METADATA_INFO,
            value=tool_data,
        )

    def get_project_name(self):
        if hasattr(self, "_tracing_service") and self.tracing_service:
            return self.tracing_service.project_name
        return "Langflow"

    def log(self, message: LoggableType | list[LoggableType], name: str | None = None) -> None:
        """记录日志。

        契约：输入日志内容与可选名称；输出无；副作用：追加日志并触发事件；
        失败语义：无。
        关键路径：1) 构造 Log 2) 写入日志列表 3) 触发 tracing/event。
        决策：默认名称自增
        问题：日志需要可区分的名称
        方案：以当前日志数量生成名称
        代价：名称与内容无语义关联
        重评：当需要语义化名称时由调用方传入
        """
        if name is None:
            name = f"Log {len(self._logs) + 1}"
        log = Log(message=message, type=get_artifact_type(message), name=name)
        self._logs.append(log)
        if self.tracing_service and self._vertex:
            self.tracing_service.add_log(trace_name=self.trace_name, log=log)
        if self._event_manager is not None and self._current_output:
            data = log.model_dump()
            data["output"] = self._current_output
            data["component_id"] = self._id
            self._event_manager.on_log(data=data)

    def _append_tool_output(self) -> None:
        if next((output for output in self.outputs if output.name == TOOL_OUTPUT_NAME), None) is None:
            self.outputs.append(
                Output(
                    name=TOOL_OUTPUT_NAME,
                    display_name=TOOL_OUTPUT_DISPLAY_NAME,
                    method="to_toolkit",
                    types=["Tool"],
                )
            )

    def is_connected_to_chat_output(self) -> bool:
        # 延迟导入以避免循环依赖
        from lfx.graph.utils import has_chat_output

        return has_chat_output(self.graph.get_vertex_neighbors(self._vertex))

    def is_connected_to_chat_input(self) -> bool:
        # 延迟导入以避免循环依赖
        from lfx.graph.utils import has_chat_input

        if self.graph is None:
            return False
        return has_chat_input(self.graph.get_vertex_neighbors(self._vertex))

    def _should_skip_message(self, message: Message) -> bool:
        """判断消息是否应跳过存储与事件。

        契约：输入 `Message`；输出 bool；副作用无；
        失败语义：无。
        关键路径：1) 检查顶点类型 2) 检查是否连接 Chat Output 3) 排除 ErrorMessage。
        决策：中间组件的非错误消息默认跳过
        问题：避免中间消息污染数据库与聊天界面
        方案：仅在输入/输出顶点或错误消息时保留
        代价：中间节点消息不可追溯
        重评：当需要全链路日志时关闭跳过
        """
        return (
            self._vertex is not None
            and not (self._vertex.is_output or self._vertex.is_input)
            and not self.is_connected_to_chat_output()
            and not isinstance(message, ErrorMessage)
        )

    def _ensure_message_required_fields(self, message: Message) -> None:
        """补齐消息存储所需字段。

        契约：输入 `Message`；输出无；副作用：可能写入默认字段；
        失败语义：无。
        关键路径：1) 补 session_id 2) 补 sender/sender_name。
        决策：仅在字段缺失时填充默认值
        问题：避免覆盖上游显式设置
        方案：字段为空时才补齐
        代价：字段缺失时依赖默认值
        重评：当需要强制字段来源时改为严格校验
        """
        from lfx.utils.constants import MESSAGE_SENDER_AI, MESSAGE_SENDER_NAME_AI

        # 若未设置 session_id，则从 graph 补齐
        if (
            not message.session_id
            and hasattr(self, "graph")
            and hasattr(self.graph, "session_id")
            and self.graph.session_id
        ):
            session_id = (
                UUID(self.graph.session_id) if isinstance(self.graph.session_id, str) else self.graph.session_id
            )
            message.session_id = session_id

        # 若未设置 sender，则使用默认 AI
        if not message.sender:
            message.sender = MESSAGE_SENDER_AI

        # 若未设置 sender_name，则使用默认 AI 名称
        if not message.sender_name:
            message.sender_name = MESSAGE_SENDER_NAME_AI

    async def send_message(self, message: Message, id_: str | None = None, *, skip_db_update: bool = False):
        """发送消息（可控制是否写库）。

        契约：输入消息与可选 id；输出存储后的消息；副作用：写库与触发事件；
        失败语义：`skip_db_update=True` 且无 ID 时抛 `ValueError`。
        关键路径：1) 跳过判断 2) 补齐字段 3) 写库或仅事件 4) 流式处理与异常清理。
        决策：流式场景可跳过 DB 更新以降低开销
        问题：频繁写库会导致性能瓶颈
        方案：提供 `skip_db_update` 快速路径
        代价：调用方需确保消息已有 ID
        重评：当数据库支持批量写入时优化策略
        """
        if self._should_skip_message(message):
            return message

        if hasattr(message, "flow_id") and isinstance(message.flow_id, str):
            message.flow_id = UUID(message.flow_id)

        # 补齐存储所需字段
        self._ensure_message_required_fields(message)

        # 若启用 skip_db_update 且已有 ID，则跳过写库（用于流式场景降低写库次数）
        if skip_db_update:
            if not message.has_id():
                msg = (
                    "skip_db_update=True requires the message to already have an ID. "
                    "The message must have been stored in the database previously."
                )
                raise ValueError(msg)

            # 创建新 Message 实例以保持一致的返回形态
            stored_message = await Message.create(**message.model_dump())
            self._stored_message_id = stored_message.get_id()
            # 仍发送事件以实时更新客户端
            # 注意：此路径未写库，无需清理 DB
            await self._send_message_event(stored_message, id_=id_)
        else:
            # 正常路径：写入/更新数据库
            stored_message = await self._store_message(message)

            # 写库后应有 ID，仍通过 get_id() 保障安全
            self._stored_message_id = stored_message.get_id()
            try:
                complete_message = ""
                if (
                    self._should_stream_message(stored_message, message)
                    and message is not None
                    and isinstance(message.text, AsyncIterator | Iterator)
                ):
                    complete_message = await self._stream_message(message.text, stored_message)
                    stored_message.text = complete_message
                    if complete_message:
                        stored_message.properties.state = "complete"
                    stored_message = await self._update_stored_message(stored_message)
                    # 注意：流式场景不再发送 complete 事件，前端已有完整内容
                else:
                    # 非流式消息才发送事件
                    await self._send_message_event(stored_message, id_=id_)
            except Exception:
                # 失败时从数据库删除消息（仅当有 ID）
                message_id = stored_message.get_id()
                if message_id:
                    await delete_message(id_=message_id)
                raise
        self.status = stored_message
        return stored_message

    async def _store_message(self, message: Message) -> Message:
        flow_id: str | None = None
        if hasattr(self, "graph"):
            # 必要时将 UUID 转为字符串
            flow_id = str(self.graph.flow_id) if self.graph.flow_id else None
        stored_messages = await astore_message(message, flow_id=flow_id)
        if len(stored_messages) != 1:
            msg = "Only one message can be stored at a time."
            raise ValueError(msg)
        stored_message = stored_messages[0]
        return await Message.create(**stored_message.model_dump())

    async def _send_message_event(self, message: Message, id_: str | None = None, category: str | None = None) -> None:
        if hasattr(self, "_event_manager") and self._event_manager:
            # 使用完整 model_dump，包含 content_blocks、properties 等字段
            data_dict = message.model_dump()

            # `message ID` 在 data_dict["data"]["id"] 中，前端需要 data_dict["id"]，因此复制到顶层
            message_id = id_ or data_dict.get("data", {}).get("id") or getattr(message, "id", None)
            if message_id and not data_dict.get("id"):
                data_dict["id"] = message_id

            category = category or data_dict.get("category", None)

            def _send_event():
                match category:
                    case "error":
                        self._event_manager.on_error(data=data_dict)
                    case "remove_message":
                        # 先检查 data_dict 是否存在 id
                        if "id" in data_dict:
                            self._event_manager.on_remove_message(data={"id": data_dict["id"]})
                        else:
                            # 若无 id，则从消息对象或参数获取
                            message_id = getattr(message, "id", None) or id_
                            if message_id:
                                self._event_manager.on_remove_message(data={"id": message_id})
                    case _:
                        self._event_manager.on_message(data=data_dict)

            await asyncio.to_thread(_send_event)

    def _should_stream_message(self, stored_message: Message, original_message: Message) -> bool:
        return bool(
            hasattr(self, "_event_manager")
            and self._event_manager
            and stored_message.has_id()
            and not isinstance(original_message.text, str)
        )

    async def _update_stored_message(self, message: Message) -> Message:
        """更新已存储的消息记录。

        契约：输入 `Message`；输出更新后的消息；副作用：写入数据库；
        失败语义：更新失败抛 `ValueError`。
        关键路径：1) 补 flow_id 2) 调用更新接口 3) 返回新消息。
        决策：优先使用 vertex.graph 的 flow_id
        问题：确保存储记录归属正确 Flow
        方案：在更新前补齐 flow_id
        代价：依赖 graph 上下文
        重评：当 flow_id 已在消息中时可跳过
        """
        if hasattr(self, "_vertex") and self._vertex is not None and hasattr(self._vertex, "graph"):
            flow_id = (
                UUID(self._vertex.graph.flow_id)
                if isinstance(self._vertex.graph.flow_id, str)
                else self._vertex.graph.flow_id
            )

            message.flow_id = flow_id

        message_tables = await aupdate_messages(message)
        if not message_tables:
            msg = "Failed to update message"
            raise ValueError(msg)
        message_table = message_tables[0]
        return await Message.create(**message_table.model_dump())

    async def _stream_message(self, iterator: AsyncIterator | Iterator, message: Message) -> str:
        if not isinstance(iterator, AsyncIterator | Iterator):
            msg = "The message must be an iterator or an async iterator."
            raise TypeError(msg)

        # 流式发送必须有消息 ID
        message_id = message.get_id()
        if not message_id:
            msg = "Message must have an ID to stream. Messages only have IDs after being stored in the database."
            raise ValueError(msg)

        if isinstance(iterator, AsyncIterator):
            return await self._handle_async_iterator(iterator, message_id, message)
        try:
            complete_message = ""
            first_chunk = True
            for chunk in iterator:
                complete_message = await self._process_chunk(
                    chunk.content, complete_message, message_id, message, first_chunk=first_chunk
                )
                first_chunk = False
        except Exception as e:
            raise StreamingError(cause=e, source=message.properties.source) from e
        else:
            return complete_message

    async def _handle_async_iterator(self, iterator: AsyncIterator, message_id: str, message: Message) -> str:
        complete_message = ""
        first_chunk = True
        async for chunk in iterator:
            complete_message = await self._process_chunk(
                chunk.content, complete_message, message_id, message, first_chunk=first_chunk
            )
            first_chunk = False
        return complete_message

    async def _process_chunk(
        self, chunk: str, complete_message: str, message_id: str, message: Message, *, first_chunk: bool = False
    ) -> str:
        complete_message += chunk
        if self._event_manager:
            if first_chunk:
                # 仅首个 chunk 发送初始消息事件
                msg_copy = message.model_copy()
                msg_copy.text = complete_message
                await self._send_message_event(msg_copy, id_=message_id)
            await asyncio.to_thread(
                self._event_manager.on_token,
                data={
                    "chunk": chunk,
                    "id": str(message_id),
                },
            )
        return complete_message

    async def send_error(
        self,
        exception: Exception,
        session_id: str,
        trace_name: str,
        source: Source,
    ) -> Message | None:
        """发送错误消息到前端。

        契约：输入异常与上下文；输出 `ErrorMessage` 或 `None`；副作用：写库并发送事件；
        失败语义：无（session_id 为空时直接返回）。
        关键路径：1) 构造 `ErrorMessage` 2) 调用 `send_message`。
        决策：无 session_id 时不发送
        问题：避免无会话消息写入
        方案：空会话直接返回
        代价：可能丢失错误消息
        重评：当需要全局错误追踪时允许无 session_id 发送
        """
        flow_id = self.graph.flow_id if hasattr(self, "graph") else None
        if not session_id:
            return None
        error_message = ErrorMessage(
            flow_id=flow_id,
            exception=exception,
            session_id=session_id,
            trace_name=trace_name,
            source=source,
        )
        await self.send_message(error_message)
        return error_message

    def _append_tool_to_outputs_map(self):
        self._outputs_map[TOOL_OUTPUT_NAME] = self._build_tool_output()
        # 若需要工具 schema，可在此追加输入（示例保留）

    def _build_tool_output(self) -> Output:
        return Output(name=TOOL_OUTPUT_NAME, display_name=TOOL_OUTPUT_DISPLAY_NAME, method="to_toolkit", types=["Tool"])

    def get_input_display_name(self, input_name: str) -> str:
        """获取输入的展示名称。

        契约：输入输入名；输出展示名；副作用无；
        失败语义：无（不存在时回退为输入名）。
        关键路径：1) 查找输入对象 2) 返回 display_name 或 name。
        决策：不存在时回退输入名
        问题：错误信息需要可读名称
        方案：优先使用 display_name
        代价：display_name 可能为空
        重评：当需要国际化时引入本地化表
        """
        if input_name in self._inputs:
            return getattr(self._inputs[input_name], "display_name", input_name)
        return input_name

    def get_output_display_name(self, output_name: str) -> str:
        """获取输出的展示名称。

        契约：输入输出名；输出展示名；副作用无；
        失败语义：无（不存在时回退为输出名）。
        关键路径：1) 查找输出对象 2) 返回 display_name 或 name。
        决策：不存在时回退输出名
        问题：错误信息需要可读名称
        方案：优先使用 display_name
        代价：display_name 可能为空
        重评：当需要国际化时引入本地化表
        """
        if output_name in self._outputs_map:
            return getattr(self._outputs_map[output_name], "display_name", output_name)
        return output_name

    def build_input_error_message(self, input_name: str, message: str) -> str:
        """构建输入错误消息。

        契约：输入输入名与消息；输出格式化字符串；副作用无；
        失败语义：无。
        关键路径：1) 获取展示名 2) 拼接错误消息。
        决策：在消息前置输入名
        问题：错误信息可能被截断
        方案：前置输入名提高可见性
        代价：消息更冗长
        重评：当错误结构化展示时改为字段化
        """
        display_name = self.get_input_display_name(input_name)
        return f"[Input: {display_name}] {message}"

    def build_output_error_message(self, output_name: str, message: str) -> str:
        """构建输出错误消息。

        契约：输入输出名与消息；输出格式化字符串；副作用无；
        失败语义：无。
        关键路径：1) 获取展示名 2) 拼接错误消息。
        决策：在消息前置输出名
        问题：错误信息可能被截断
        方案：前置输出名提高可见性
        代价：消息更冗长
        重评：当错误结构化展示时改为字段化
        """
        display_name = self.get_output_display_name(output_name)
        return f"[Output: {display_name}] {message}"

    def build_component_error_message(self, message: str) -> str:
        """构建组件错误消息。

        契约：输入消息；输出格式化字符串；副作用无；
        失败语义：无。
        关键路径：1) 获取组件展示名 2) 拼接错误消息。
        决策：在消息前置组件名
        问题：错误信息可能被截断
        方案：前置组件名提高可见性
        代价：消息更冗长
        重评：当错误结构化展示时改为字段化
        """
        return f"[Component: {self.display_name or self.__class__.__name__}] {message}"


def _get_component_toolkit():
    from lfx.base.tools.component_tool import ComponentToolkit

    return ComponentToolkit
