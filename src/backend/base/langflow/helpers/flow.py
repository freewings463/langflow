"""
模块名称：Flow 查询与执行辅助

本模块提供 Flow 的查询、加载、执行与动态函数生成能力。
主要功能包括：
- 按用户/文件夹/名称查询 Flow
- 加载 Flow 并构建 Graph 执行
- 生成动态工具函数与输入 schema
- 构建用于 API 的 JSON Schema

关键组件：
- `load_flow` / `run_flow`
- `generate_function_for_flow`
- `json_schema_from_flow`

设计背景：需要在 API 与工具层复用统一的 Flow 执行入口。
注意事项：动态函数使用 `exec` 生成，需确保输入来源可信。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from fastapi import HTTPException
from lfx.log.logger import logger
from pydantic.v1 import BaseModel, Field, create_model
from sqlalchemy.orm import aliased
from sqlmodel import asc, desc, select

from langflow.schema.schema import INPUT_FIELD_NAME
from langflow.services.database.models.flow.model import Flow, FlowRead
from langflow.services.deps import get_settings_service, session_scope

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from lfx.graph.graph.base import Graph
    from lfx.graph.schema import RunOutputs
    from lfx.graph.vertex.base import Vertex

from langflow.schema.data import Data

INPUT_TYPE_MAP = {
    "ChatInput": {"type_hint": "Optional[str]", "default": '""'},
    "TextInput": {"type_hint": "Optional[str]", "default": '""'},
    "JSONInput": {"type_hint": "Optional[dict]", "default": "{}"},
}
SORT_DISPATCHER = {
    "asc": asc,
    "desc": desc,
}


async def list_flows(*, user_id: str | None = None) -> list[Data]:
    """列出用户的非组件流程。

    契约：`user_id` 必填，返回 `Data` 列表。
    失败语义：无会话或查询失败抛 `ValueError`。

    决策：过滤 `is_component=False` 的流程
    问题：组件型流程不应出现在常规列表
    方案：查询时排除组件记录
    代价：无法直接从此接口获取组件流程
    重评：若需要统一入口时增加可选参数
    """
    if not user_id:
        msg = "Session is invalid"
        raise ValueError(msg)
    try:
        async with session_scope() as session:
            uuid_user_id = UUID(user_id) if isinstance(user_id, str) else user_id
            stmt = select(Flow).where(Flow.user_id == uuid_user_id).where(Flow.is_component == False)  # noqa: E712
            flows = (await session.exec(stmt)).all()

            return [flow.to_data() for flow in flows]
    except Exception as e:
        msg = f"Error listing flows: {e}"
        raise ValueError(msg) from e


async def list_flows_by_flow_folder(
    *,
    user_id: str | None = None,
    flow_id: str | None = None,
    order_params: dict | None = {"column": "updated_at", "direction": "desc"},  # noqa: B006
) -> list[Data]:
    """列出与指定 Flow 同文件夹的其他流程。

    契约：`user_id` 与 `flow_id` 必填，返回 `Data` 列表。
    关键路径（三步）：
    1) 找到目标 Flow 的 `folder_id`
    2) 查询同一文件夹下的其他 Flow
    3) 按 `order_params` 排序
    失败语义：参数缺失或查询失败抛 `ValueError`。

    决策：默认按 `updated_at desc` 排序
    问题：列表展示需要最近更新优先
    方案：未传入排序时采用默认值
    代价：无法反映其他排序策略
    重评：当 UI 允许自定义排序时调整
    """
    if not user_id:
        msg = "Session is invalid"
        raise ValueError(msg)
    if not flow_id:
        msg = "Flow ID is required"
        raise ValueError(msg)
    try:
        async with session_scope() as session:
            uuid_user_id = UUID(user_id) if isinstance(user_id, str) else user_id
            uuid_flow_id = UUID(flow_id) if isinstance(flow_id, str) else flow_id
            flow_ = aliased(Flow)
            stmt = (
                select(Flow.id, Flow.name, Flow.updated_at)
                .join(flow_, Flow.folder_id == flow_.folder_id)
                .where(flow_.id == uuid_flow_id)
                .where(flow_.user_id == uuid_user_id)
                .where(Flow.user_id == uuid_user_id)
                .where(Flow.id != uuid_flow_id)
            )
            if order_params is not None:
                sort_col = getattr(Flow, order_params.get("column", "updated_at"), Flow.updated_at)
                sort_dir = SORT_DISPATCHER.get(order_params.get("direction", "desc"), desc)
                stmt = stmt.order_by(sort_dir(sort_col))

            flows = (await session.exec(stmt)).all()
            return [Data(data=dict(flow._mapping)) for flow in flows]  # noqa: SLF001
    except Exception as e:
        msg = f"Error listing flows: {e}"
        raise ValueError(msg) from e


async def list_flows_by_folder_id(
    *, user_id: str | None = None, folder_id: str | None = None, order_params: dict | None = None
) -> list[Data]:
    """列出指定文件夹下的流程。

    契约：`user_id` 与 `folder_id` 必填。
    关键路径（三步）：
    1) 校验参数
    2) 查询文件夹内的 Flow
    3) 按 `order_params` 排序
    失败语义：参数缺失或查询失败抛 `ValueError`。

    决策：默认按 `updated_at desc` 排序
    问题：列表展示需要稳定排序
    方案：`order_params` 为空时使用默认值
    代价：默认策略可能不符合所有场景
    重评：当引入多字段排序时扩展
    """
    if not user_id:
        msg = "Session is invalid"
        raise ValueError(msg)
    if not folder_id:
        msg = "Folder ID is required"
        raise ValueError(msg)

    if order_params is None:
        order_params = {"column": "updated_at", "direction": "desc"}

    try:
        async with session_scope() as session:
            uuid_user_id = UUID(user_id) if isinstance(user_id, str) else user_id
            uuid_folder_id = UUID(folder_id) if isinstance(folder_id, str) else folder_id
            stmt = (
                select(Flow.id, Flow.name, Flow.updated_at)
                .where(Flow.user_id == uuid_user_id)
                .where(Flow.folder_id == uuid_folder_id)
            )
            if order_params is not None:
                sort_col = getattr(Flow, order_params.get("column", "updated_at"), Flow.updated_at)
                sort_dir = SORT_DISPATCHER.get(order_params.get("direction", "desc"), desc)
                stmt = stmt.order_by(sort_dir(sort_col))

            flows = (await session.exec(stmt)).all()
            return [Data(data=dict(flow._mapping)) for flow in flows]  # noqa: SLF001
    except Exception as e:
        msg = f"Error listing flows: {e}"
        raise ValueError(msg) from e


async def get_flow_by_id_or_name(
    *,
    user_id: str | None = None,
    flow_id: str | None = None,
    flow_name: str | None = None,
) -> Data | None:
    """按 ID 或名称获取单个流程。

    契约：`flow_id` 或 `flow_name` 至少提供一个，返回 `Data` 或 `None`。
    失败语义：参数缺失或查询失败抛 `ValueError`。

    决策：同时提供时优先使用 `flow_id`
    问题：名称可能不唯一或可变
    方案：以 ID 作为强一致主键
    代价：调用方需要管理 ID
    重评：若引入唯一名称约束可调整策略
    """
    if not user_id:
        msg = "Session is invalid"
        raise ValueError(msg)
    if not (flow_id or flow_name):
        msg = "Flow ID or Flow Name is required"
        raise ValueError(msg)

    attr, val = None, None
    if flow_name:
        attr = "name"
        val = flow_name
    if flow_id:
        attr = "id"
        val = flow_id
    if not (attr and val):
        msg = "Flow id or Name is required"
        raise ValueError(msg)
    try:
        async with session_scope() as session:
            uuid_user_id = UUID(user_id) if isinstance(user_id, str) else user_id  # type: ignore[assignment]
            uuid_flow_id_or_name = val  # type: ignore[assignment]
            if isinstance(val, str) and attr == "id":
                uuid_flow_id_or_name = UUID(val)  # type: ignore[assignment]
            stmt = select(Flow).where(Flow.user_id == uuid_user_id).where(getattr(Flow, attr) == uuid_flow_id_or_name)
            flow = (await session.exec(stmt)).first()
            return flow.to_data() if flow else None

    except Exception as e:
        msg = f"Error getting flow by id: {e}"
        raise ValueError(msg) from e


async def load_flow(
    user_id: str, flow_id: str | None = None, flow_name: str | None = None, tweaks: dict | None = None
) -> Graph:
    """加载 Flow 并构建 Graph。

    契约：`flow_id` 或 `flow_name` 必填，返回 `Graph`。
    关键路径（三步）：
    1) 解析名称并定位 `flow_id`
    2) 读取数据库中的 `flow.data`
    3) 应用 `tweaks` 并构建 Graph
    失败语义：找不到流程抛 `ValueError`。

    决策：仅在 `flow_name` 场景下调用 `find_flow`
    问题：名称与 ID 的输入路径不同
    方案：优先使用已有 `flow_id`，否则查找名称
    代价：名称查询额外一次 DB 调用
    重评：当提供统一标识符时简化
    """
    from lfx.graph.graph.base import Graph

    from langflow.processing.process import process_tweaks

    if not flow_id and not flow_name:
        msg = "Flow ID or Flow Name is required"
        raise ValueError(msg)
    if not flow_id and flow_name:
        flow_id = await find_flow(flow_name, user_id)
        if not flow_id:
            msg = f"Flow {flow_name} not found"
            raise ValueError(msg)

    async with session_scope() as session:
        graph_data = flow.data if (flow := await session.get(Flow, flow_id)) else None
    if not graph_data:
        msg = f"Flow {flow_id} not found"
        raise ValueError(msg)
    if tweaks:
        graph_data = process_tweaks(graph_data=graph_data, tweaks=tweaks)
    return Graph.from_payload(graph_data, flow_id=flow_id, user_id=user_id)


async def find_flow(flow_name: str, user_id: str) -> str | None:
    """按名称查找 Flow 的 ID。

    契约：找到返回 ID，未找到返回 `None`。
    失败语义：查询失败向上抛异常。

    决策：未找到时返回 `None` 而非抛错
    问题：名称查询常用于“可选”路径
    方案：由调用方决定是否报错
    代价：调用方需要显式处理空值
    重评：若改为强制存在可抛 404
    """
    async with session_scope() as session:
        uuid_user_id = UUID(user_id) if isinstance(user_id, str) else user_id
        stmt = select(Flow).where(Flow.name == flow_name).where(Flow.user_id == uuid_user_id)
        flow = (await session.exec(stmt)).first()
        return flow.id if flow else None


async def run_flow(
    inputs: dict | list[dict] | None = None,
    tweaks: dict | None = None,
    flow_id: str | None = None,
    flow_name: str | None = None,
    output_type: str | None = "chat",
    user_id: str | None = None,
    run_id: str | None = None,
    session_id: str | None = None,
    graph: Graph | None = None,
) -> list[RunOutputs]:
    """执行 Flow 并返回运行输出。

    契约：返回 `RunOutputs` 列表；`inputs` 为单个或列表均可。
    关键路径（三步）：
    1) 解析输入并构建 `inputs_list`
    2) 计算输出节点列表
    3) 调用 `graph.arun` 执行
    失败语义：`user_id` 缺失抛 `ValueError`。

    决策：输出节点按 `output_type` 过滤
    问题：不同调用方需要不同输出粒度
    方案：支持 `debug/any/chat` 等模式
    代价：调用方需理解输出命名规则
    重评：当输出类型规范化后简化过滤逻辑
    """
    if user_id is None:
        msg = "Session is invalid"
        raise ValueError(msg)
    if graph is None:
        graph = await load_flow(user_id, flow_id, flow_name, tweaks)
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
        inputs_list.append({INPUT_FIELD_NAME: cast("str", input_dict.get("input_value"))})
        inputs_components.append(input_dict.get("components", []))
        types.append(input_dict.get("type", "chat"))

    outputs = [
        vertex.id
        for vertex in graph.vertices
        if output_type == "debug"
        or (
            vertex.is_output and (output_type == "any" or output_type in vertex.id.lower())  # type: ignore[operator]
        )
    ]

    fallback_to_env_vars = get_settings_service().settings.fallback_to_env_var

    return await graph.arun(
        inputs_list,
        outputs=outputs,
        inputs_components=inputs_components,
        types=types,
        fallback_to_env_vars=fallback_to_env_vars,
    )


def generate_function_for_flow(
    inputs: list[Vertex], flow_id: str, user_id: str | UUID | None
) -> Callable[..., Awaitable[Any]]:
    """根据输入节点生成动态异步函数。

    契约：返回可 `await` 的函数，用于执行指定 Flow。
    关键路径（三步）：
    1) 生成参数签名与参数映射
    2) 拼接函数体并 `compile`
    3) 通过 `exec` 注入并返回函数
    失败语义：编译或执行失败抛异常。

    决策：使用 `exec` 动态生成函数
    问题：需要动态参数签名以匹配输入节点
    方案：运行时拼接函数源码并编译
    代价：可读性与安全性降低
    重评：若支持 `inspect.Signature` 动态函数再替换
    """
    args = [
        (
            f"{input_.display_name.lower().replace(' ', '_')}: {INPUT_TYPE_MAP[input_.base_name]['type_hint']} = "
            f"{INPUT_TYPE_MAP[input_.base_name]['default']}"
        )
        for input_ in inputs
    ]

    original_arg_names = [input_.display_name for input_ in inputs]

    func_args = ", ".join(args)

    arg_mappings = ", ".join(
        f'"{original_name}": {name}'
        for original_name, name in zip(original_arg_names, [arg.split(":")[0] for arg in args], strict=True)
    )

    func_body = f"""
from typing import Optional
async def flow_function({func_args}):
    tweaks = {{ {arg_mappings} }}
    from langflow.helpers.flow import run_flow
    from langchain_core.tools import ToolException
    from lfx.base.flow_processing.utils import build_data_from_result_data, format_flow_output_data
    try:
        run_outputs = await run_flow(
            tweaks={{key: {{'input_value': value}} for key, value in tweaks.items()}},
            flow_id="{flow_id}",
            user_id="{user_id}"
        )
        if not run_outputs:
                return []
        run_output = run_outputs[0]

        data = []
        if run_output is not None:
            for output in run_output.outputs:
                if output:
                    data.extend(build_data_from_result_data(output))
        return format_flow_output_data(data)
    except Exception as e:
        raise ToolException(f'Error running flow: ' + e)
"""

    compiled_func = compile(func_body, "<string>", "exec")
    local_scope: dict = {}
    exec(compiled_func, globals(), local_scope)  # noqa: S102
    return local_scope["flow_function"]


def build_function_and_schema(
    flow_data: Data, graph: Graph, user_id: str | UUID | None
) -> tuple[Callable[..., Awaitable[Any]], type[BaseModel]]:
    """为 Flow 构建动态函数与输入 schema。

    契约：返回 `(async function, pydantic schema)`。
    关键路径（三步）：
    1) 提取输入节点
    2) 生成动态执行函数
    3) 基于输入构建 schema
    失败语义：输入解析或生成失败抛异常。

    决策：函数与 schema 同步生成
    问题：工具调用需要同时具备执行与参数说明
    方案：在同一入口返回两者
    代价：生成逻辑耦合在一起
    重评：若需要独立缓存可拆分
    """
    flow_id = flow_data.id
    inputs = get_flow_inputs(graph)
    dynamic_flow_function = generate_function_for_flow(inputs, flow_id, user_id=user_id)
    schema = build_schema_from_inputs(flow_data.name, inputs)
    return dynamic_flow_function, schema


def get_flow_inputs(graph: Graph) -> list[Vertex]:
    """获取 Graph 的输入节点列表。

    契约：返回 `vertex.is_input=True` 的节点列表。
    失败语义：无异常处理，依赖 Graph 数据正确性。

    决策：以 `is_input` 作为唯一判定条件
    问题：输入节点需要统一筛选规则
    方案：使用图结构的布尔标记
    代价：标记错误会导致漏选或误选
    重评：若引入输入类型枚举则改为类型判断
    """
    return [vertex for vertex in graph.vertices if vertex.is_input]


def build_schema_from_inputs(name: str, inputs: list[Vertex]) -> type[BaseModel]:
    """根据输入节点生成 Pydantic schema。

    契约：字段名来自 `display_name` 的下划线形式。
    失败语义：输入结构异常时抛 `ValidationError`。

    决策：所有字段类型统一为 `str`
    问题：输入节点的真实类型可能复杂
    方案：先以字符串类型表达并保留描述
    代价：类型精度不足
    重评：当输入类型映射完善后按真实类型生成
    """
    fields = {}
    for input_ in inputs:
        field_name = input_.display_name.lower().replace(" ", "_")
        description = input_.description
        fields[field_name] = (str, Field(default="", description=description))
    return create_model(name, **fields)


def get_arg_names(inputs: list[Vertex]) -> list[dict[str, str]]:
    """返回输入节点的参数名映射列表。

    契约：输出为 `component_name` 与 `arg_name` 的字典列表。
    失败语义：不抛异常，依赖输入节点结构。

    决策：参数名使用显示名的下划线形式
    问题：原始显示名可能包含空格
    方案：统一替换为空下划线
    代价：可能与其他字段产生同名冲突
    重评：若引入唯一 slug 规则则替换
    """
    return [
        {"component_name": input_.display_name, "arg_name": input_.display_name.lower().replace(" ", "_")}
        for input_ in inputs
    ]


async def get_flow_by_id_or_endpoint_name(flow_id_or_name: str, user_id: str | UUID | None = None) -> FlowRead | None:
    """按 ID 或 endpoint_name 获取 Flow。

    契约：找到时返回 `FlowRead`，否则抛 `HTTPException(404)`。
    失败语义：无匹配记录抛 404。

    决策：ID 优先解析为 UUID
    问题：同一参数需要支持两种标识方式
    方案：先尝试 UUID，失败后当作 endpoint
    代价：异常路径会触发一次解析失败
    重评：若引入显式前缀区分可优化
    """
    async with session_scope() as session:
        endpoint_name = None
        try:
            flow_id = UUID(flow_id_or_name)
            flow = await session.get(Flow, flow_id)
        except ValueError:
            endpoint_name = flow_id_or_name
            stmt = select(Flow).where(Flow.endpoint_name == endpoint_name)
            if user_id:
                uuid_user_id = UUID(user_id) if isinstance(user_id, str) else user_id
                stmt = stmt.where(Flow.user_id == uuid_user_id)
            flow = (await session.exec(stmt)).first()
        if flow is None:
            raise HTTPException(status_code=404, detail=f"Flow identifier {flow_id_or_name} not found")
        return FlowRead.model_validate(flow, from_attributes=True)


async def generate_unique_flow_name(flow_name, user_id, session):
    """生成不与现有 Flow 重名的名称。

    契约：在发现同名时追加 ` (n)`，直到唯一。
    失败语义：数据库查询失败向上抛异常。

    决策：采用递增后缀 `(n)` 规避冲突
    问题：用户输入名称可能重复
    方案：循环查询并追加计数
    代价：重复多时会多次查询
    重评：若支持数据库唯一约束 + 重试可简化
    """
    original_name = flow_name
    n = 1
    while True:
        existing_flow = (
            await session.exec(
                select(Flow).where(
                    Flow.name == flow_name,
                    Flow.user_id == user_id,
                )
            )
        ).first()

        if not existing_flow:
            return flow_name

        flow_name = f"{original_name} ({n})"
        n += 1


def json_schema_from_flow(flow: Flow) -> dict:
    """从 Flow 输入节点生成 JSON Schema。

    契约：仅包含可见且非高级字段，返回 JSON Schema 字典。
    关键路径（三步）：
    1) 从 `flow.data` 构建 Graph
    2) 遍历输入节点模板字段
    3) 生成 `properties` 与 `required`
    失败语义：字段类型未知时降级为 `string` 并记录日志。

    决策：未知字段类型降级为 `string`
    问题：模板字段类型可能未在映射表中
    方案：记录警告并回退类型
    代价：Schema 精度降低
    重评：当字段类型表完整时移除降级
    """
    from lfx.graph.graph.base import Graph

    flow_data = flow.data or {}

    graph = Graph.from_payload(flow_data)
    input_nodes = [vertex for vertex in graph.vertices if vertex.is_input]

    properties = {}
    required = []
    for node in input_nodes:
        node_data = node.data["node"]
        template = node_data["template"]

        for field_name, field_data in template.items():
            if field_data != "Component" and field_data.get("show", False) and not field_data.get("advanced", False):
                field_type = field_data.get("type", "string")
                properties[field_name] = {
                    "type": field_type,
                    "description": field_data.get("info", f"Input for {field_name}"),
                }
                if field_type == "str":
                    field_type = "string"
                elif field_type == "int":
                    field_type = "integer"
                elif field_type == "float":
                    field_type = "number"
                elif field_type == "bool":
                    field_type = "boolean"
                else:
                    logger.warning(f"Unknown field type: {field_type} defaulting to string")
                    field_type = "string"
                properties[field_name]["type"] = field_type

                if field_data.get("required", False):
                    required.append(field_name)

    return {"type": "object", "properties": properties, "required": required}
