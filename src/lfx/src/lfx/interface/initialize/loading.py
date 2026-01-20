"""
模块名称：组件初始化与构建

本模块负责组件实例化、参数加载与构建结果产出，支持从数据库或环境变量加载敏感字段。
主要功能包括：
- 动态实例化自定义组件
- 处理 `load_from_db` 字段并回退环境变量
- 构建组件结果与产物信息

关键组件：
- `instantiate_class`：实例化组件
- `update_params_with_load_from_db_fields`：参数加载与回退
- `build_component`/`build_custom_component`：构建执行结果

设计背景：统一组件构建流程，支持多环境下的变量加载策略。
注意事项：数据库不可用时会回退到环境变量读取。
"""

"""
模块名称：组件实例化与加载流程

本模块提供自定义组件实例化、参数解析、数据库变量加载与构建结果处理能力。主要功能包括：
- 根据 Vertex 与代码生成组件实例
- 解析与规范化组件参数（含 JSON/集合转换）
- 支持从数据库或环境变量加载敏感字段
- 构建组件并生成可序列化的输出与工件

关键组件：
- `instantiate_class`：根据代码与参数实例化组件
- `update_params_with_load_from_db_fields`：加载变量并更新参数
- `build_component` / `build_custom_component`：执行构建并返回结果

设计背景：统一组件构建入口，兼容不同类型组件与数据来源。
使用场景：图执行时创建组件实例并产出结果。
注意事项：数据库不可用时会回退环境变量；构建过程可能抛出异常需上层处理。
"""

from __future__ import annotations

import inspect
import os
import warnings
from typing import TYPE_CHECKING, Any

import orjson
from pydantic import PydanticDeprecatedSince20

from lfx.custom.eval import eval_custom_component_code
from lfx.log.logger import logger
from lfx.schema.artifact import get_artifact_type, post_process_raw
from lfx.schema.data import Data
from lfx.services.deps import get_settings_service, session_scope
from lfx.services.session import NoopSession

if TYPE_CHECKING:
    from lfx.custom.custom_component.component import Component
    from lfx.custom.custom_component.custom_component import CustomComponent
    from lfx.graph.vertex.base import Vertex

    # 注意：前向声明用于避免循环导入。
    class EventManager:
        pass


def instantiate_class(
    vertex: Vertex,
    user_id=None,
    event_manager: EventManager | None = None,
) -> Any:
    """根据 Vertex 配置实例化组件类。

    契约：返回 `(component_instance, custom_params)`；`vertex.params` 必须包含 `code`。
    副作用：执行动态代码并创建组件实例。
    关键路径（三步）：1) 解析参数 2) 执行代码获取类 3) 实例化并注入事件管理器。
    失败语义：缺少 `base_type` 或代码执行失败会抛异常。
    决策：使用 `eval_custom_component_code` 动态执行组件代码。
    问题：组件实现由用户提供，需运行时解析。
    方案：从 `code` 生成类并注入 Vertex 参数。
    代价：运行时执行代码存在安全与稳定性风险。
    重评：当引入沙箱或预编译机制时调整。
    """
    vertex_type = vertex.vertex_type
    base_type = vertex.base_type
    logger.debug(f"Instantiating {vertex_type} of type {base_type}")

    if not base_type:
        msg = "No base type provided for vertex"
        raise ValueError(msg)

    custom_params = get_params(vertex.params)
    code = custom_params.pop("code")
    class_object: type[CustomComponent | Component] = eval_custom_component_code(code)
    custom_component: CustomComponent | Component = class_object(
        _user_id=user_id,
        _parameters=custom_params,
        _vertex=vertex,
        _tracing_service=None,
        _id=vertex.id,
    )
    if hasattr(custom_component, "set_event_manager"):
        custom_component.set_event_manager(event_manager)
    return custom_component, custom_params


async def get_instance_results(
    custom_component,
    custom_params: dict,
    vertex: Vertex,
    *,
    fallback_to_env_vars: bool = False,
    base_type: str = "component",
):
    """构建组件并返回结果与产物。

    契约：根据 `base_type` 分流构建；返回构建结果与工件。
    副作用：可能访问数据库/环境变量并触发组件构建。
    关键路径（三步）：1) 加载变量并更新参数 2) 选择构建分支 3) 返回结果。
    失败语义：未知 `base_type` 抛 `ValueError`；构建异常上抛。
    决策：区分 `custom_components` 与 `component` 构建路径。
    问题：自定义组件与内置组件构建流程不同。
    方案：按 `base_type` 分流至对应函数。
    代价：分支逻辑增加维护成本。
    重评：当构建流程统一后合并。
    """
    custom_params = await update_params_with_load_from_db_fields(
        custom_component,
        custom_params,
        vertex.load_from_db_fields,
        fallback_to_env_vars=fallback_to_env_vars,
    )
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=PydanticDeprecatedSince20)
        if base_type == "custom_components":
            return await build_custom_component(params=custom_params, custom_component=custom_component)
        if base_type == "component":
            return await build_component(params=custom_params, custom_component=custom_component)
        msg = f"Base type {base_type} not found."
        raise ValueError(msg)


def get_params(vertex_params):
    """标准化节点参数并返回副本。

    契约：返回新的参数副本，包含集合与 JSON 字符串的转换结果。
    副作用：无（不修改原对象）。
    失败语义：JSON 解析失败将丢弃对应字段。
    决策：集中处理参数预处理逻辑。
    问题：参数格式不统一会导致构建失败。
    方案：统一调用 `convert_params_to_sets` 与 `convert_kwargs`。
    代价：预处理增加计算成本。
    重评：当参数来源统一后简化。
    """
    params = vertex_params
    params = convert_params_to_sets(params)
    params = convert_kwargs(params)
    return params.copy()


def convert_params_to_sets(params):
    """将特定参数转换为集合类型。

    契约：处理 `allowed_special` 与 `disallowed_special` 字段。
    副作用：原地修改 params。
    失败语义：无。
    决策：将列表转为集合以便后续高效判断。
    问题：某些参数在下游需要集合语义。
    方案：在参数预处理阶段统一转换。
    代价：集合无序且序列化成本更高。
    重评：当下游支持列表语义时移除转换。
    """
    if "allowed_special" in params:
        params["allowed_special"] = set(params["allowed_special"])
    if "disallowed_special" in params:
        params["disallowed_special"] = set(params["disallowed_special"])
    return params


def convert_kwargs(params):
    """解析 `kwargs`/`config` 字符串为 JSON。

    契约：对 key 含 `kwargs`/`config` 且值为字符串的字段尝试 JSON 解析。
    副作用：原地修改 params，并移除解析失败的键。
    失败语义：JSON 解析失败会删除对应字段。
    决策：解析失败时移除字段以避免后续构建异常。
    问题：无效 JSON 会在构建阶段导致错误。
    方案：捕获解析异常并剔除无效字段。
    代价：可能丢失用户输入且难以追踪。
    重评：当引入严格参数校验时改为显式报错。
    """
    # 注意：遍历时暂存待删除项，避免原地修改。
    items_to_remove = []
    for key, value in params.items():
        if ("kwargs" in key or "config" in key) and isinstance(value, str):
            try:
                params[key] = orjson.loads(value)
            except orjson.JSONDecodeError:
                items_to_remove.append(key)

    # 注意：遍历结束后移除无效键，避免迭代过程中修改。
    for key in items_to_remove:
        params.pop(key, None)

    return params


def load_from_env_vars(params, load_from_db_fields, context=None):
    """从环境变量或上下文加载字段值。

    契约：仅处理 `load_from_db_fields` 指定字段；成功则覆盖 params 中对应值。
    副作用：读取环境变量并修改 params。
    关键路径（三步）：1) 优先请求上下文 2) 回退环境变量 3) 写回参数。
    失败语义：变量不存在时写入 None 并记录日志。
    决策：请求上下文优先于环境变量。
    问题：同名变量在请求内可能需要覆盖全局值。
    方案：先查 `request_variables`，再查环境变量。
    代价：上下文缺失时仍需读取环境变量。
    重评：当引入集中变量服务时替换数据源。
    """
    for field in load_from_db_fields:
        if field not in params or not params[field]:
            continue
        variable_name = params[field]
        key = None

        # 实现：优先读取请求上下文中的变量。
        if context and "request_variables" in context:
            request_variables = context["request_variables"]
            if variable_name in request_variables:
                key = request_variables[variable_name]
                logger.debug(f"Found context override for variable '{variable_name}'")

        if key is None:
            key = os.getenv(variable_name)
            if key:
                logger.info(f"Using environment variable {variable_name} for {field}")
            else:
                logger.error(f"Environment variable {variable_name} is not set.")
        params[field] = key if key is not None else None
        if key is None:
            logger.warning(f"Could not get value for {field}. Setting it to None.")
    return params


async def update_table_params_with_load_from_db_fields(
    custom_component: CustomComponent,
    params: dict,
    table_field_name: str,
    *,
    fallback_to_env_vars: bool = False,
) -> dict:
    """更新表格字段的 `load_from_db` 列值。

    契约：仅处理 `table_field_name` 指定表格字段；返回更新后的 params。
    副作用：访问数据库或环境变量，并修改 params。
    关键路径（三步）：1) 解析表格与列元信息 2) 逐行加载变量 3) 写回表格数据。
    失败语义：加载失败时将该列值置为 None 并记录日志。
    决策：数据库不可用时回退环境变量。
    问题：无数据库会话时仍需提供变量值。
    方案：检测 NoopSession 并改用环境变量。
    代价：环境变量可能缺失导致空值。
    重评：当统一使用变量服务时移除回退。
    """
    # 实现：读取表格数据与列元信息。
    table_data = params.get(table_field_name, [])
    metadata_key = f"{table_field_name}_load_from_db_columns"
    load_from_db_columns = params.pop(metadata_key, [])

    if not table_data or not load_from_db_columns:
        return params

    # 注意：仅获取一次上下文，避免重复访问。
    context = None
    if hasattr(custom_component, "graph") and hasattr(custom_component.graph, "context"):
        context = custom_component.graph.context

    async with session_scope() as session:
        settings_service = get_settings_service()
        is_noop_session = isinstance(session, NoopSession) or (
            settings_service and settings_service.settings.use_noop_database
        )

        # 实现：逐行处理表格数据。
        updated_table_data = []
        for row in table_data:
            if not isinstance(row, dict):
                updated_table_data.append(row)
                continue

            updated_row = row.copy()

            # 实现：处理需要数据库加载的列。
            for column_name in load_from_db_columns:
                if column_name not in updated_row:
                    continue

                # 注意：列值为需要查询的变量名。
                variable_name = updated_row[column_name]
                if not variable_name:
                    continue

                try:
                    if is_noop_session:
                        # 注意：无数据库时回退环境变量。
                        key = None
                        # 实现：优先读取请求上下文变量。
                        if context and "request_variables" in context:
                            request_variables = context["request_variables"]
                            if variable_name in request_variables:
                                key = request_variables[variable_name]
                                logger.debug(f"Found context override for variable '{variable_name}'")

                        if key is None:
                            key = os.getenv(variable_name)
                            if key:
                                logger.info(
                                    f"Using environment variable {variable_name} for table column {column_name}"
                                )
                            else:
                                logger.error(f"Environment variable {variable_name} is not set.")
                    else:
                        # 实现：从数据库读取变量。
                        key = await custom_component.get_variable(
                            name=variable_name, field=f"{table_field_name}.{column_name}", session=session
                        )

                except ValueError as e:
                    if "User id is not set" in str(e):
                        raise
                    logger.debug(str(e))
                    key = None

                # 注意：若数据库无值且允许回退，则读取环境变量。
                if fallback_to_env_vars and key is None:
                    key = os.getenv(variable_name)
                    if key:
                        logger.info(f"Using environment variable {variable_name} for table column {column_name}")
                    else:
                        logger.error(f"Environment variable {variable_name} is not set.")

                # 实现：写回解析后的列值。
                updated_row[column_name] = key if key is not None else None
                if key is None:
                    logger.warning(
                        f"Could not get value for {variable_name} in table column {column_name}. Setting it to None."
                    )

            updated_table_data.append(updated_row)

        params[table_field_name] = updated_table_data
        return params


async def update_params_with_load_from_db_fields(
    custom_component: CustomComponent,
    params,
    load_from_db_fields,
    *,
    fallback_to_env_vars=False,
):
    """处理字段级 `load_from_db` 并回退环境变量。

    契约：支持普通字段与表格字段（`table:` 前缀）。
    副作用：访问数据库/环境变量并修改 params。
    关键路径（三步）：1) 判断数据库可用性 2) 逐字段加载变量 3) 写回参数。
    失败语义：特定错误抛出，其余加载失败写入 None 并记录日志。
    决策：数据库不可用时整体回退到环境变量。
    问题：无数据库环境仍需组件可运行。
    方案：检测 NoopSession 并使用 env vars。
    代价：失去数据库变量更新能力。
    重评：当所有部署均具备数据库时移除回退。
    """
    async with session_scope() as session:
        settings_service = get_settings_service()
        is_noop_session = isinstance(session, NoopSession) or (
            settings_service and settings_service.settings.use_noop_database
        )
        if is_noop_session:
            logger.debug("Loading variables from environment variables because database is not available.")
            context = None
            if hasattr(custom_component, "graph") and hasattr(custom_component.graph, "context"):
                context = custom_component.graph.context
            return load_from_env_vars(params, load_from_db_fields, context=context)
        for field in load_from_db_fields:
            # 注意：表格字段使用 `table:` 前缀。
            if field.startswith("table:"):
                table_field_name = field[6:]  # 注意：移除 `table:` 前缀。
                params = await update_table_params_with_load_from_db_fields(
                    custom_component,
                    params,
                    table_field_name,
                    fallback_to_env_vars=fallback_to_env_vars,
                )
            else:
                # 实现：处理普通字段加载。
                if field not in params or not params[field]:
                    continue

                try:
                    key = await custom_component.get_variable(name=params[field], field=field, session=session)
                except ValueError as e:
                    if any(reason in str(e) for reason in ["User id is not set", "variable not found."]):
                        raise
                    logger.debug(str(e))
                    key = None

                if fallback_to_env_vars and key is None:
                    key = os.getenv(params[field])
                    if key:
                        logger.info(f"Using environment variable {params[field]} for {field}")
                    else:
                        logger.error(f"Environment variable {params[field]} is not set.")

                params[field] = key if key is not None else None
                if key is None:
                    logger.warning(f"Could not get value for {field}. Setting it to None.")

        return params


async def build_component(
    params: dict,
    custom_component: Component,
):
    """构建内置组件并返回结果与产物。

    契约：返回 `(custom_component, build_results, artifacts)`。
    副作用：设置组件属性并执行构建。
    失败语义：构建异常原样上抛。
    决策：先 `set_attributes` 再 `build_results`。
    问题：组件构建依赖参数注入。
    方案：先注入参数，再执行构建。
    代价：参数注入失败会阻断构建。
    重评：当引入更严格参数校验时拆分步骤。
    """
    # 实现：设置参数并执行构建。
    custom_component.set_attributes(params)
    build_results, artifacts = await custom_component.build_results()

    return custom_component, build_results, artifacts


async def build_custom_component(params: dict, custom_component: CustomComponent):
    """构建自定义组件并返回结果与工件。

    契约：返回 `(custom_component, build_result, artifact)`；组件需绑定 vertex。
    副作用：执行组件 build，生成 artifact 并写入组件结果。
    关键路径（三步）：1) 执行 build 2) 生成 repr/raw 3) 写入 artifact 与结果。
    失败语义：无 vertex 时抛 `ValueError`；构建异常上抛。
    决策：兼容同步与异步 build 方法。
    问题：自定义组件实现可能为同步或异步。
    方案：检测 `iscoroutinefunction` 决定调用方式。
    代价：分支逻辑增加维护成本。
    重评：当统一为异步 build 时简化调用。
    """
    if "retriever" in params and hasattr(params["retriever"], "as_retriever"):
        params["retriever"] = params["retriever"].as_retriever()

    # 注意：判断 `build` 是否为异步方法。
    is_async = inspect.iscoroutinefunction(custom_component.build)

    # 注意：输出选择由 vertex 连接关系决定，方法参数已注入组件实例。

    if is_async:
        # 实现：异步构建。
        build_result = await custom_component.build(**params)
    else:
        # 实现：同步构建。
        build_result = custom_component.build(**params)
    custom_repr = custom_component.custom_repr()
    if custom_repr is None and isinstance(build_result, dict | Data | str):
        custom_repr = build_result
    if not isinstance(custom_repr, str):
        custom_repr = str(custom_repr)
    raw = custom_component.repr_value
    if hasattr(raw, "data") and raw is not None:
        raw = raw.data

    elif hasattr(raw, "model_dump") and raw is not None:
        raw = raw.model_dump()
    if raw is None and isinstance(build_result, dict | Data | str):
        raw = build_result.data if isinstance(build_result, Data) else build_result

    artifact_type = get_artifact_type(custom_component.repr_value or raw, build_result)
    raw = post_process_raw(raw, artifact_type)
    artifact = {"repr": custom_repr, "raw": raw, "type": artifact_type}

    if custom_component.get_vertex() is not None:
        custom_component.set_artifacts({custom_component.get_vertex().outputs[0].get("name"): artifact})
        custom_component.set_results({custom_component.get_vertex().outputs[0].get("name"): build_result})
        return custom_component, build_result, artifact

    msg = "Custom component does not have a vertex"
    raise ValueError(msg)
