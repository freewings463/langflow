"""
模块名称：自定义组件初始化与构建

本模块提供自定义组件的实例化、参数补齐与构建结果的流程封装，主要用于在运行时把 `Vertex` 转换为可执行组件。主要功能包括：
- 解析 `vertex.params` 并补齐运行时参数
- 从数据库或环境变量加载敏感字段
- 执行组件 `build` 并生成 `artifact`

关键组件：
- `instantiate_class`：从自定义代码实例化组件
- `update_params_with_load_from_db_fields`：按字段规则加载变量
- `build_custom_component`：构建并生成 `artifact`

设计背景：自定义组件以代码字符串存储，需要受控地评估并注入运行时依赖
注意事项：执行 `eval_custom_component_code` 可能抛出异常；日志关键字包含 `Instantiating` 与 `Environment variable`
"""

from __future__ import annotations

import inspect
import os
import warnings
from typing import TYPE_CHECKING, Any

import orjson
from lfx.custom.eval import eval_custom_component_code
from lfx.log.logger import logger
from pydantic import PydanticDeprecatedSince20

from langflow.schema.artifact import get_artifact_type, post_process_raw
from langflow.schema.data import Data
from langflow.services.deps import get_tracing_service, session_scope

if TYPE_CHECKING:
    from lfx.custom.custom_component.component import Component
    from lfx.custom.custom_component.custom_component import CustomComponent
    from lfx.graph.vertex.base import Vertex

    from langflow.events.event_manager import EventManager


def instantiate_class(
    vertex: Vertex,
    user_id=None,
    event_manager: EventManager | None = None,
) -> Any:
    """从 `Vertex` 实例化自定义组件。

    契约：输入 `vertex`/`user_id`/`event_manager`；输出组件实例与参数字典；副作用为创建组件对象。
    关键路径（三步）：
    1) 校验 `vertex.base_type` 并整理参数
    2) 使用 `eval_custom_component_code` 解析自定义代码
    3) 实例化组件并注入事件管理器
    异常流：`base_type` 为空抛 `ValueError`；缺少 `code` 抛 `KeyError`；代码评估失败抛出其原始异常。
    性能瓶颈：自定义代码评估与组件构造。
    排障入口：日志关键字 `Instantiating`。
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
        _tracing_service=get_tracing_service(),
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
    """根据 `base_type` 构建组件并返回结果。

    契约：输入组件实例/参数/`vertex`/`base_type`；输出构建结果元组；副作用为参数更新与组件状态变化。
    关键路径（三步）：
    1) 根据 `load_from_db_fields` 补齐参数
    2) 根据 `base_type` 选择构建函数
    3) 执行构建并返回结果
    异常流：`base_type` 未识别抛 `ValueError`；构建失败抛出其原始异常。
    性能瓶颈：数据库变量加载与组件 `build`。
    排障入口：关注 `update_params_with_load_from_db_fields` 的 `Environment variable`/`Could not get value` 日志。
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
    """规范化 `vertex.params` 并返回副本。

    契约：输入 `dict` 参数；输出清洗后的新字典。
    关键路径：集合字段转换 + `kwargs/config` JSON 解析。
    失败语义：无显式异常处理；解析失败的键会被移除。
    """
    params = vertex_params
    params = convert_params_to_sets(params)
    params = convert_kwargs(params)
    return params.copy()


def convert_params_to_sets(params):
    """将特定字段转为 `set`。

    契约：输入参数字典；输出同一对象引用（原地更新）。
    失败语义：字段类型不支持 `set()` 时由调用方处理。
    """
    if "allowed_special" in params:
        params["allowed_special"] = set(params["allowed_special"])
    if "disallowed_special" in params:
        params["disallowed_special"] = set(params["disallowed_special"])
    return params


def convert_kwargs(params):
    """解析 `kwargs`/`config` 字段中的 JSON 字符串。

    契约：输入参数字典；输出同一对象引用（原地更新）。
    关键路径：解析失败时移除对应键以避免无效配置。
    失败语义：`orjson.JSONDecodeError` 被捕获并导致键移除。
    """
    items_to_remove = []
    for key, value in params.items():
        if ("kwargs" in key or "config" in key) and isinstance(value, str):
            try:
                params[key] = orjson.loads(value)
            except orjson.JSONDecodeError:
                items_to_remove.append(key)

    for key in items_to_remove:
        params.pop(key, None)

    return params


async def update_params_with_load_from_db_fields(
    custom_component: Component,
    params,
    load_from_db_fields,
    *,
    fallback_to_env_vars=False,
):
    """按字段规则从数据库或环境变量加载参数。

    契约：输入组件/参数/字段列表；输出更新后的参数字典；副作用为数据库读取与环境变量访问。
    关键路径（三步）：
    1) 遍历 `load_from_db_fields` 并调用 `get_variable`
    2) 失败时按 `fallback_to_env_vars` 决定是否读环境变量
    3) 写回参数并记录告警日志
    异常流：用户未设置时抛 `ValueError`；变量不存在且未启用 fallback 时抛 `ValueError`。
    性能瓶颈：数据库查询与逐字段日志。
    排障入口：日志关键字 `Environment variable`/`Could not get value`.
    """
    async with session_scope() as session:
        for field in load_from_db_fields:
            if field not in params or not params[field]:
                continue

            try:
                key = await custom_component.get_variable(name=params[field], field=field, session=session)
            except ValueError as e:
                if "User id is not set" in str(e):
                    raise
                if "variable not found." in str(e) and not fallback_to_env_vars:
                    raise
                await logger.adebug(str(e))
                key = None

            if fallback_to_env_vars and key is None:
                key = os.getenv(params[field])
                if key:
                    await logger.ainfo(f"Using environment variable {params[field]} for {field}")
                else:
                    await logger.aerror(f"Environment variable {params[field]} is not set.")

            params[field] = key if key is not None else None
            if key is None:
                await logger.awarning(f"Could not get value for {field}. Setting it to None.")

        return params


async def build_component(
    params: dict,
    custom_component: Component,
):
    """构建标准组件并返回结果与 `artifact`。

    契约：输入参数与组件；输出 `(component, build_results, artifacts)`；副作用为组件属性写入。
    失败语义：组件 `build_results` 失败时抛出其原始异常。
    """
    custom_component.set_attributes(params)
    build_results, artifacts = await custom_component.build_results()

    return custom_component, build_results, artifacts


async def build_custom_component(params: dict, custom_component: CustomComponent):
    """构建自定义组件并生成 `artifact`。

    契约：输入参数与组件；输出 `(component, build_result, artifact)`；副作用为组件结果与 `artifact` 写入。
    关键路径（三步）：
    1) 预处理 `retriever` 并选择同步/异步构建路径
    2) 生成 `repr`/`raw` 并推断 `artifact_type`
    3) 绑定 `vertex` 输出并写入结果
    异常流：组件缺少 `vertex` 时抛 `ValueError`；`build` 失败抛出其原始异常。
    性能瓶颈：`build` 执行与 `artifact` 后处理。
    排障入口：关注 `Custom component does not have a vertex` 错误信息。
    """
    if "retriever" in params and hasattr(params["retriever"], "as_retriever"):
        params["retriever"] = params["retriever"].as_retriever()

    is_async = inspect.iscoroutinefunction(custom_component.build)

    if is_async:
        build_result = await custom_component.build(**params)
    else:
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

    vertex = custom_component.get_vertex()
    if vertex is not None:
        custom_component.set_artifacts({vertex.outputs[0].get("name"): artifact})
        custom_component.set_results({vertex.outputs[0].get("name"): build_result})
        return custom_component, build_result, artifact

    msg = "Custom component does not have a vertex"
    raise ValueError(msg)
