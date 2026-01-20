"""
模块名称：运行 Flow 工具组件

本模块提供 `RunFlowBaseComponent`，用于选择 `Flow`、同步输入输出、缓存图结构，
并将 `Flow` 作为工具暴露给 `agent`。主要功能包括：
- 获取并缓存 `Flow` 图与元信息
- 根据图动态生成输入/输出字段
- 执行 `Flow` 并解析输出

关键组件：`RunFlowBaseComponent`、`_flow_cache_call`、`_format_flow_outputs`
设计背景：运行 `Flow` 需要统一的输入映射与缓存策略
注意事项：缓存依赖用户 ID 与 `flow_id`；动态输出方法需与 `output` 名一致
"""

from collections import Counter
from datetime import datetime
from types import MethodType  # 注意：用于动态绑定输出解析方法
from typing import TYPE_CHECKING, Any

from langflow.helpers.flow import get_flow_by_id_or_name
from langflow.processing.process import process_tweaks_on_graph

from lfx.base.tools.constants import TOOL_OUTPUT_NAME
from lfx.custom.custom_component.component import Component, get_component_toolkit
from lfx.field_typing import Tool
from lfx.graph.graph.base import Graph
from lfx.graph.vertex.base import Vertex

# TODO：切换到 `lfx` 统一实现
from lfx.helpers import get_flow_inputs, run_flow
from lfx.inputs.inputs import BoolInput, DropdownInput, InputTypes, MessageTextInput, StrInput
from lfx.log.logger import logger
from lfx.schema.data import Data
from lfx.schema.dotdict import dotdict
from lfx.services.cache.utils import CacheMiss
from lfx.services.deps import get_shared_component_cache_service
from lfx.template.field.base import Output

if TYPE_CHECKING:
    from collections.abc import Callable

    from lfx.base.tools.component_tool import ComponentToolkit


class RunFlowBaseComponent(Component):
    """`Flow` 运行基础组件，负责输入输出映射、缓存与工具化。
    契约：输入为 `flow` 选择与 `tweaks`；输出为动态生成的 `Flow` 输出或工具。
    关键路径：获取图 → 同步输入/输出 → 执行 `flow` → 解析结果。
    决策：缓存共享图以减少重复构建。问题：频繁加载图成本高；方案：共享缓存；代价：缓存失效处理复杂；重评：当图结构变更频繁时。
    """

    def __init__(self, *args, **kwargs):
        self._flow_output_methods: set[str] = set()
        super().__init__(*args, **kwargs)
        self.add_tool_output = True
        ################################################################
        # 注意：当 `cache_flow` 启用时，将选中 `Flow` 图缓存到共享缓存中。
        ################################################################
        self._shared_component_cache = get_shared_component_cache_service()
        # 注意：注册缓存相关方法；`_flow_cache_call` 提供统一校验入口。
        self._cache_flow_dispatcher: dict[str, Callable[..., Any]] = {
            "get": self._get_cached_flow,
            "set": self._set_cached_flow,
            "delete": self._delete_cached_flow,
            "_build_key": self._build_flow_cache_key,
            "_build_graph": self._build_graph_from_dict,
        }
        # 注意：缓存最近一次运行结果，避免多输出重复执行。
        self._last_run_outputs: list[Data] | None = None
        # 注意：记录选中 `Flow` 的 `updated_at`，用于缓存一致性检查。
        self._cached_flow_updated_at: str | None = None

    _base_inputs: list[InputTypes] = [
        DropdownInput(
            name="flow_name_selected",
            display_name="Flow Name",
            info="The name of the flow to run.",
            options=[],
            options_metadata=[],
            real_time_refresh=True,
            refresh_button=True,
            value=None,
        ),
        StrInput(
            name="flow_id_selected",
            display_name="Flow ID",
            info="The ID of the flow to run.",
            value=None,
            show=False,
            override_skip=True,  # 注意：运行期需要保留该字段
        ),
        MessageTextInput(
            name="session_id",
            display_name="Session ID",
            info="The session ID to run the flow in.",
            advanced=True,
        ),
        # 注意：是否缓存 `Flow` 的开关。
        # 说明：`flow_name_selected` 刷新会自动更新选中 `Flow`。
        # TODO：提供更显式的缓存更新方式。
        BoolInput(
            name="cache_flow",
            display_name="Cache Flow",
            info="Whether to cache the selected flow.",
            value=False,
            advanced=True,
        ),
    ]
    _base_outputs: list[Output] = []
    default_keys = ["code", "_type", "flow_name_selected", "flow_id_selected", "session_id", "cache_flow"]
    FLOW_INPUTS: list[dotdict] = []
    flow_tweak_data: dict = {}
    IOPUT_SEP = "~"  # 注意：连接 `vertex_id` 与输入/输出名的分隔符

    ################################################################
    # 设置并注册选中 `Flow` 的输出方法
    ################################################################
    def map_outputs(self) -> None:  # 注意：重写基础 `map_outputs`
        """注册 Flow 动态输出方法。
        契约：在 outputs 映射中注册动态解析方法。
        关键路径：调用父类 → 生成动态方法。
        决策：每次 map_outputs 前清理旧方法。问题：避免方法泄漏；方案：先清理后注册；代价：重复注册开销；重评：当输出稳定且可缓存时。
        """
        super().map_outputs()
        self._ensure_flow_output_methods()

    def _ensure_flow_output_methods(self) -> None:
        self._clear_dynamic_flow_output_methods()
        for output in self._outputs_map.values():
            if not output or not output.name or output.name == TOOL_OUTPUT_NAME or self.IOPUT_SEP not in output.name:
                continue
            vertex_id, output_name = output.name.split(self.IOPUT_SEP, 1)
            output.method = self._register_flow_output_method(
                vertex_id=vertex_id,
                output_name=output_name,
            )

    ################################################################
    # `Flow` 获取
    ################################################################
    async def get_flow(self, flow_name_selected: str | None = None, flow_id_selected: str | None = None) -> Data:
        """按名称或 `ID` 获取 `Flow` 数据。
        契约：返回 `Data`，找不到时返回空 `Data`。
        关键路径：按 `flow_id`/`flow_name` 拉取 → 空值兜底。
        决策：找不到时返回空 `Data` 而非抛错。问题：`UI` 可能需要容错；方案：空数据兜底；代价：调用方需自行判断；重评：当需要强失败语义时。
        """
        flow = await get_flow_by_id_or_name(
            user_id=self.user_id,
            flow_id=flow_id_selected,
            flow_name=flow_name_selected,
        )
        return flow or Data(data={})

    async def get_graph(
        self,
        flow_name_selected: str | None = None,
        flow_id_selected: str | None = None,
        updated_at: str | None = None,
    ) -> Graph | None:
        """按名称或 ID 获取 `Flow` 图。
        契约：返回 `Graph`；不存在时抛 `ValueError`。
        关键路径：读取缓存 → 校验更新时间 → 拉取 `Flow` → 构建 `Graph` → 缓存。
        决策：命中缓存且未过期直接返回。问题：避免重复构建；方案：缓存校验；代价：更新时间不一致时需清理；重评：当缓存一致性无法保证时。
        """
        if not (flow_name_selected or flow_id_selected):
            msg = "Flow name or id is required"
            raise ValueError(msg)
        if flow_id_selected and (flow := self._flow_cache_call("get", flow_id=flow_id_selected)):
            if self._is_cached_flow_up_to_date(flow, updated_at):
                return flow
            self._flow_cache_call("delete", flow_id=flow_id_selected)  # 注意：过期缓存需删除

        # TODO：仅使用 `flow_id` 路径
        flow = await self.get_flow(flow_name_selected=flow_name_selected, flow_id_selected=flow_id_selected)
        if not flow:
            msg = "Flow not found"
            raise ValueError(msg)

        graph = Graph.from_payload(
            payload=flow.data.get("data", {}),
            flow_id=flow_id_selected,
            flow_name=flow_name_selected,
        )
        graph.description = flow.data.get("description", None)
        graph.updated_at = flow.data.get("updated_at", None)

        self._flow_cache_call("set", flow=graph)

        return graph

    ################################################################
    # `Flow` 输入与配置
    ################################################################
    def get_new_fields_from_graph(self, graph: Graph) -> list[dotdict]:
        """从图生成新的输入字段列表。
        契约：返回 dotdict 列表，用于构建组件输入。
        关键路径：提取 `Flow` 输入 → 转换为字段模板。
        决策：直接基于 `get_flow_inputs` 转换。问题：保持与 `Flow` 输入一致；方案：复用工具函数；代价：依赖外部实现；重评：当输入解析策略变化时。
        """
        inputs = get_flow_inputs(graph)
        return self.get_new_fields(inputs)

    def update_build_config_from_graph(self, build_config: dotdict, graph: Graph):
        """基于图更新 `build_config` 的输入字段。
        契约：原地更新 `build_config`；失败抛 `RuntimeError`。
        关键路径：生成新字段 → 计算保留键 → 删除旧字段 → 写入新字段。
        决策：仅保留新字段与默认键。问题：旧字段可能失效；方案：清理后重建；代价：丢失旧值；重评：当需要保留历史输入时。
        """
        try:
            new_fields = self.get_new_fields_from_graph(graph)
            keep_fields: set[str] = set([new_field["name"] for new_field in new_fields] + self.default_keys)
            self.delete_fields(build_config, [key for key in build_config if key not in keep_fields])
            build_config.update((field["name"], field) for field in new_fields)
        except Exception as e:
            msg = "Error updating build config from graph"
            logger.exception(msg)
            raise RuntimeError(msg) from e

    def get_new_fields(self, inputs_vertex: list[Vertex]) -> list[dotdict]:
        """将输入节点转换为字段模板列表。
        契约：返回字段 dotdict 列表；缺少模板时跳过。
        关键路径：遍历输入顶点 → 读取模板 → 生成字段列表。
        决策：`display_name` 冲突时附加顶点标识。问题：同名字段易混淆；方案：追加顶点名/`ID`；代价：显示变长；重评：当 `UI` 支持分组展示时。
        """
        new_fields: list[dotdict] = []
        vdisp_cts = Counter(v.display_name for v in inputs_vertex)

        for vertex in inputs_vertex:
            field_template = vertex.data.get("node", {}).get("template", {})
            field_order = vertex.data.get("node", {}).get("field_order", [])
            if not (field_order and field_template):
                continue
            new_vertex_inputs = [
                dotdict(
                    {
                        **field_template[input_name],
                        "name": self._get_ioput_name(vertex.id, input_name),
                        "display_name": (
                            f"{field_template[input_name]['display_name']} ({vertex.display_name})"
                            if vdisp_cts[vertex.display_name] == 1
                            else (
                                f"{field_template[input_name]['display_name']}"
                                f"({vertex.display_name}-{vertex.id.split('-')[-1]})"
                            )
                        ),
                        # TODO：提升生成规则的健壮性
                        "tool_mode": not (field_template[input_name].get("advanced", False)),
                    }
                )
                for input_name in field_order
                if input_name in field_template
            ]
            new_fields += new_vertex_inputs
        return new_fields

    def add_new_fields(self, build_config: dotdict, new_fields: list[dotdict]) -> dotdict:
        """向 `build_config` 添加新字段。
        契约：原地写入并返回 `build_config`。
        关键路径：遍历新字段 → 覆盖写入。
        决策：不做重复检测直接覆盖。问题：字段可能冲突；方案：后写覆盖；代价：旧值丢失；重评：当需要合并策略时。
        """
        for field in new_fields:
            build_config[field["name"]] = field
        return build_config

    def delete_fields(self, build_config: dotdict, fields: dict | list[str]) -> None:
        """删除 `build_config` 中指定字段。
        契约：支持传入 dict 或字段名列表；不存在时忽略。
        关键路径：规范字段列表 → 逐项 pop 删除。
        决策：缺失字段不报错。问题：批量删除时避免噪声；方案：pop 默认 None；代价：静默忽略；重评：当需要强校验时。
        """
        if isinstance(fields, dict):
            fields = list(fields.keys())
        for field in fields:
            build_config.pop(field, None)

    async def get_required_data(self) -> tuple[str, list[dotdict]] | None:
        """获取 `Flow` 描述与 `tool_mode` 输入字段。
        契约：返回 (description, tool_mode_fields)；找不到 `flow` 时返回 `None`。
        关键路径：获取 `graph` → 同步输出 → 生成字段 → 过滤 `tool_mode`。
        决策：仅暴露非 `advanced` 字段作为 `tool_mode`。问题：避免暴露复杂配置；方案：过滤 `advanced`；代价：功能受限；重评：当需要高级工具输入时。
        """
        graph = await self.get_graph(self.flow_name_selected, self.flow_id_selected, self._cached_flow_updated_at)
        formatted_outputs = self._format_flow_outputs(graph)
        self._sync_flow_outputs(formatted_outputs)
        new_fields = self.get_new_fields_from_graph(graph)
        new_fields = self.update_input_types(new_fields)

        return (graph.description, [field for field in new_fields if field.get("tool_mode") is True])

    def update_input_types(self, fields: list[dotdict]) -> list[dotdict]:
        """修正字段的 `input_types`。
        契约：将 `None` 规范为 [] 并返回更新后的字段列表。
        关键路径：遍历字段 → 修正 `input_types`。
        决策：`None` 统一转空列表。问题：前端不接受 `None`；方案：规范化；代价：掩盖上游配置问题；重评：当上游保证一致性时。
        """
        for field in fields:
            if isinstance(field, dict):
                if field.get("input_types", None) is None:
                    field["input_types"] = []
            elif hasattr(field, "input_types") and field.input_types is None:
                field.input_types = []
        return fields

    async def _get_tools(self) -> list[Tool]:
        """将 `Flow` 作为工具暴露。
        契约：返回 `Tool` 列表；无 `tool_mode` 输入时返回空列表。
        """
        component_toolkit: type[ComponentToolkit] = get_component_toolkit()
        flow_description, tool_mode_inputs = await self.get_required_data()
        if not tool_mode_inputs:
            return []
        # 注意：将 `dict` 列表转为 `dotdict` 列表。
        tool_mode_inputs = [dotdict(field) for field in tool_mode_inputs]
        return component_toolkit(component=self).get_tools(
            tool_name=f"{self.flow_name_selected}_tool",
            tool_description=(
                f"Tool designed to execute the flow '{self.flow_name_selected}'. Flow details: {flow_description}."
            ),
            callbacks=self.get_langchain_callbacks(),
            flow_mode_inputs=tool_mode_inputs,
        )

    ################################################################
    # `Flow` 输出解析
    ################################################################
    async def _get_cached_run_outputs(
        self,
        *,
        user_id: str | None = None,
        tweaks: dict | None,
        inputs: dict | list[dict] | None,
        output_type: str,
    ):
        if self._last_run_outputs is not None:
            return self._last_run_outputs
        resolved_tweaks = tweaks or self.flow_tweak_data or {}
        resolved_inputs = (inputs or self._flow_run_inputs or self._build_inputs_from_tweaks(resolved_tweaks)) or None
        self._last_run_outputs = await self._run_flow_with_cached_graph(
            user_id=user_id,
            tweaks=resolved_tweaks,
            inputs=resolved_inputs,
            output_type=output_type,
        )
        return self._last_run_outputs

    async def _resolve_flow_output(self, *, vertex_id: str, output_name: str):
        """解析指定顶点输出值。
        契约：返回输出值或 `None`。
        决策：仅从第一个匹配输出返回结果。问题：避免多值混淆；方案：首匹配返回；代价：忽略后续结果；重评：当需要聚合所有输出时。
        """
        run_outputs = await self._get_cached_run_outputs(
            user_id=self.user_id,
            tweaks=self.flow_tweak_data,
            inputs=None,
            output_type="any",
        )

        if not run_outputs:
            return None
        first_output = run_outputs[0]
        if not first_output.outputs:
            return None
        for result in first_output.outputs:
            if not (result and result.component_id == vertex_id):
                continue
            if isinstance(result.results, dict) and output_name in result.results:
                return result.results[output_name]
            if result.artifacts and output_name in result.artifacts:
                return result.artifacts[output_name]
            return result.results or result.artifacts or result.outputs

        return None

    def _clear_dynamic_flow_output_methods(self) -> None:
        for method_name in self._flow_output_methods:
            if hasattr(self, method_name):
                delattr(self, method_name)
        self._flow_output_methods.clear()

    def _register_flow_output_method(self, *, vertex_id: str, output_name: str) -> str:
        safe_vertex = vertex_id.replace("-", "_")
        safe_output = output_name.replace("-", "_").replace(self.IOPUT_SEP, "_")
        method_name = f"_resolve_flow_output__{safe_vertex}__{safe_output}"

        async def _dynamic_resolver(_self):
            return await _self._resolve_flow_output(  # noqa: SLF001
                vertex_id=vertex_id,
                output_name=output_name,
            )

        setattr(self, method_name, MethodType(_dynamic_resolver, self))
        self._flow_output_methods.add(method_name)
        return method_name

    ################################################################
    # 动态输出同步
    ################################################################
    def _sync_flow_outputs(self, outputs: list[Output]) -> None:
        """同步并持久化动态输出列表。
        契约：更新 `outputs` 与 `_outputs_map`；保留 `TOOL_OUTPUT_NAME`。
        决策：始终保留工具输出。问题：工具输出被覆盖会失效；方案：强制写回；代价：需额外合并逻辑；重评：当工具输出独立管理时。
        """
        tool_output = None
        if TOOL_OUTPUT_NAME in self._outputs_map:
            tool_output = self._outputs_map[TOOL_OUTPUT_NAME]
        else:
            tool_output = next(
                (out for out in outputs if out and out.name == TOOL_OUTPUT_NAME),
                None,
            )

        self.outputs = outputs
        self._outputs_map = {out.name: out for out in outputs if out}
        self._outputs_map[TOOL_OUTPUT_NAME] = tool_output

    async def update_outputs(self, frontend_node: dict, field_name: str, field_value: Any) -> dict:
        """更新前端节点的输出列表。
        契约：仅在 `flow_name_selected` 变更时更新并返回节点。
        关键路径：读取选中 `Flow` → 生成输出 → 同步到前端节点。
        决策：字段无效时直接返回原节点。问题：避免不必要的重建；方案：提前返回；代价：依赖字段判断准确性；重评：当需要更强制的同步策略时。
        """
        if field_name != "flow_name_selected" or not field_value:
            return frontend_node

        flow_selected_metadata = (
            frontend_node.get("template", {}).get("flow_name_selected", {}).get("selected_metadata", {})
        )
        graph = await self.get_graph(
            flow_name_selected=field_value,
            flow_id_selected=flow_selected_metadata.get("id"),
            updated_at=flow_selected_metadata.get("updated_at"),
        )
        outputs = self._format_flow_outputs(graph)  # 注意：从输出节点生成 `Output` 列表
        self._sync_flow_outputs(outputs)
        frontend_node["outputs"] = [output.model_dump() for output in outputs]
        return frontend_node

    ################################################################
    # 工具模式与输出格式化
    ################################################################
    def _format_flow_outputs(self, graph: Graph) -> list[Output]:
        """从图的输出节点生成 `Output` 列表。
        契约：返回包含动态 `method` 与唯一 `name` 的 `Output` 列表。
        决策：输出名使用 `vertex_id` 前缀。问题：避免同名输出冲突；方案：前缀拼接；代价：名称变长；重评：当 `UI` 支持分组展示时。
        """
        output_vertices: list[Vertex] = [v for v in graph.vertices if v.is_output]
        outputs: list[Output] = []
        vdisp_cts = Counter(v.display_name for v in output_vertices)
        for vertex in output_vertices:
            one_out = len(vertex.outputs) == 1
            for vertex_output in vertex.outputs:
                new_name = self._get_ioput_name(vertex.id, vertex_output.get("name"))
                output = Output(**vertex_output)
                output.name = new_name
                output.method = self._register_flow_output_method(
                    vertex_id=vertex.id,
                    output_name=vertex_output.get("name"),
                )
                vdn = vertex.display_name
                odn = output.display_name
                output.display_name = (
                    vdn
                    if one_out and vdisp_cts[vdn] == 1
                    else odn
                    + (
                        # 注意：`display_name` 可能与其它顶点冲突
                        f" ({vdn})"
                        if vdisp_cts[vdn] == 1
                        # 注意：重复顶点时用 `vertex_id` 区分
                        else f"-{vertex.id}"
                    )
                )
                outputs.append(output)

        return outputs

    def _get_ioput_name(
        self,
        vertex_id: str,
        ioput_name: str,
    ) -> str:
        """拼接 `vertex_id` 与输入/输出名，生成唯一名称。
        契约：`vertex_id` 与 `ioput_name` 缺失时抛 `ValueError`。
        决策：使用固定分隔符拼接。问题：避免名称冲突；方案：统一分隔符；代价：依赖分隔符不冲突；重评：当字段名包含分隔符时。
        """
        if not vertex_id or not ioput_name:
            msg = "Vertex ID and input/output name are required"
            raise ValueError(msg)
        return f"{vertex_id}{self.IOPUT_SEP}{ioput_name}"

    ################################################################
    # `Flow` 执行
    ################################################################
    async def _run_flow_with_cached_graph(
        self,
        *,
        user_id: str | None = None,
        tweaks: dict | None = None,
        inputs: dict | list[dict] | None = None,
        output_type: str = "any",  # 注意：`any` 用于返回全部输出
    ):
        graph = await self.get_graph(
            flow_name_selected=self.flow_name_selected,
            flow_id_selected=self.flow_id_selected,
            updated_at=self._cached_flow_updated_at,
        )
        if tweaks:
            graph = process_tweaks_on_graph(graph, tweaks)

        return await run_flow(
            inputs=inputs,
            flow_id=self.flow_id_selected,
            flow_name=self.flow_name_selected,
            user_id=user_id,
            session_id=self.session_id,
            output_type=output_type,
            graph=graph,
        )

    ################################################################
    # `Flow` 缓存工具
    ################################################################
    def _flow_cache_call(self, action: str, *args, **kwargs):
        """调用 `Flow` 缓存相关方法。
        契约：缓存关闭或缓存服务不可用时返回 `None`。
        决策：缓存失败时记录告警并返回 `None`。问题：避免缓存影响主流程；方案：软失败；代价：缓存命中率下降；重评：当缓存是强依赖时。
        """
        if not self.cache_flow:
            msg = "Cache flow is disabled"
            logger.warning(msg)
            return None
        if self._shared_component_cache is None:
            logger.warning("Shared component cache is not available")
            return None

        handler = self._cache_flow_dispatcher.get(action)
        if handler is None:
            msg = f"Unknown cache action: {action}"
            raise ValueError(msg)
        try:
            return handler(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            key = kwargs.get("cache_key") or kwargs.get("flow_name") or kwargs.get("flow_name_selected")
            if not key and args:
                key = args[0]
            logger.warning("Cache %s failed for key %s: %s", action, key or "[missing key]", exc)
            return None

    def _get_cached_flow(self, *, flow_id: str | None = None) -> Graph | None:
        cache_key = self._build_flow_cache_key(flow_id=flow_id)
        cache_entry = self._shared_component_cache.get(cache_key)
        if isinstance(cache_entry, CacheMiss):
            logger.debug(f"{cache_entry} for key {cache_key}")
            return None
        if not cache_entry:
            logger.warning(f"None or empty cache entry ({cache_entry}) for key {cache_key}")
            return None
        return self._build_graph_from_dict(cache_entry=cache_entry)

    def _set_cached_flow(self, *, flow: Graph) -> None:
        graph_dump = flow.dump()
        payload = {
            "graph_dump": graph_dump,
            "flow_id": flow.flow_id,
            "user_id": self.user_id,
            "description": flow.description or graph_dump.get("description"),
            "updated_at": flow.updated_at or graph_dump.get("updated_at"),
        }
        cache_key = self._build_flow_cache_key(flow_id=flow.flow_id)
        self._shared_component_cache.set(cache_key, payload)

    def _build_flow_cache_key(self, *, flow_id: str | None = None) -> str | None:
        """构建 `Flow` 缓存 key。
        契约：缺少 `user_id` 或 `flow_id` 时抛 `ValueError`。
        决策：key 包含 `user_id` 与 `flow_id`。问题：隔离不同用户缓存；方案：拼接标识；代价：key 依赖 `user_id`；重评：当改为全局缓存时。
        """
        if not (self.user_id and flow_id):
            msg = "Failed to build cache key: Flow ID and user ID are required"
            raise ValueError(msg)
        return f"run_flow:{self.user_id}:{flow_id or 'missing_id'}"

    def _build_graph_from_dict(self, *, cache_entry: dict[str, Any]) -> Graph | None:
        if not (graph_dump := cache_entry.get("graph_dump")):
            return None
        graph = Graph.from_payload(
            payload=graph_dump.get("data", {}),
            flow_id=cache_entry.get("flow_id"),
            flow_name=cache_entry.get("flow_name"),
            user_id=cache_entry.get("user_id"),
        )
        graph.description = cache_entry.get("description") or graph_dump.get("description")
        graph.updated_at = cache_entry.get("updated_at") or graph_dump.get("updated_at")
        return graph

    def _is_cached_flow_up_to_date(self, cached_flow: Graph, updated_at: str | None) -> bool:
        if not updated_at or not (cached_ts := getattr(cached_flow, "updated_at", None)):
            return False  # 注意：两侧时间戳必须都存在
        return self._parse_timestamp(cached_ts) >= self._parse_timestamp(updated_at)

    @staticmethod
    def _parse_timestamp(value: str | None) -> datetime | None:
        from datetime import timezone

        if not value:
            return None
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.replace(tzinfo=timezone.utc, microsecond=0)
        except ValueError:
            logger.warning("Invalid updated_at value: %s", value)
            return None

    def _delete_cached_flow(self, flow_id: str | None) -> None:
        """从缓存中删除指定 `Flow`。
        契约：缺少 `user_id`/`flow_id` 时抛 `ValueError`。
        决策：严格校验 `flow_id`。问题：避免误删；方案：强校验；代价：调用方需保证输入；重评：当支持按名称删除时。
        """
        err_msg_prefix = "Failed to delete user flow from cache"
        if self._shared_component_cache is None:
            msg = f"{err_msg_prefix}: Shared component cache is not available"
            raise ValueError(msg)
        if not self.user_id:
            msg = f"{err_msg_prefix}: Please provide your user ID"
            raise ValueError(msg)
        if not flow_id or not flow_id.strip():
            msg = f"{err_msg_prefix}: Please provide a valid flow ID"
            raise ValueError(msg)

        self._shared_component_cache.delete(self._build_flow_cache_key(flow_id=flow_id))

    ################################################################
    # 构建输入与 `tweak` 数据
    ################################################################
    def _extract_tweaks_from_keyed_values(
        self,
        values: dict[str, Any] | None,
    ) -> dict[str, dict[str, Any]]:
        tweaks: dict[str, dict[str, Any]] = {}
        if not values:
            return tweaks
        for field_name, field_value in values.items():
            if self.IOPUT_SEP not in field_name:
                continue
            node_id, param_name = field_name.split(self.IOPUT_SEP, 1)
            tweaks.setdefault(node_id, {})[param_name] = field_value
        return tweaks

    def _build_inputs_from_tweaks(
        self,
        tweaks: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        inputs: list[dict[str, Any]] = []
        for vertex_id, params in tweaks.items():
            if "input_value" not in params:
                continue
            payload: dict[str, Any] = {
                "components": [vertex_id],
                "input_value": params["input_value"],
            }
            if params.get("type"):
                payload["type"] = params["type"]
            inputs.append(payload)
        return inputs

    def _get_selected_flow_updated_at(self) -> str | None:
        updated_at = (
            getattr(self, "_vertex", {})
            .data.get("node", {})
            .get("template", {})
            .get("flow_name_selected", {})
            .get("selected_metadata", {})
            .get("updated_at", None)
        )
        if updated_at:
            return updated_at
        return self._attributes.get("flow_name_selected_updated_at")

    def _pre_run_setup(self) -> None:  # 注意：重写基础 pre_run_setup
        """新一次执行前的准备工作。
        契约：清空上次运行缓存并重建 `tweaks`/`inputs`。
        决策：每次执行前清空 `_last_run_outputs`。问题：避免复用过期输出；方案：强制清空；代价：无法重用缓存；重评：当需要跨次复用时。
        """
        self._last_run_outputs = None
        self._cached_flow_updated_at = self._get_selected_flow_updated_at()
        if self._cached_flow_updated_at:
            self._attributes["flow_name_selected_updated_at"] = self._cached_flow_updated_at
        self._attributes["flow_tweak_data"] = {}
        self.flow_tweak_data = self._extract_tweaks_from_keyed_values(self._attributes)
        self._flow_run_inputs = self._build_inputs_from_tweaks(self.flow_tweak_data)
