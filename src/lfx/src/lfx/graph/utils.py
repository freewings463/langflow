"""
模块名称：图执行辅助工具

本模块提供图运行过程中的通用工具函数与日志记录逻辑。
主要功能包括：
- 处理提示词输入变量与默认补全
- 识别产物类型并进行后处理
- 记录交易/构建日志（可选持久化）

关键组件：
- `get_artifact_type`：产物类型识别
- `log_transaction`/`log_vertex_build`：可观测日志写入
- `_vertex_to_primitive_dict`：参数净化

设计背景：将跨图执行的辅助逻辑集中，避免重复实现。
注意事项：日志写入依赖可选服务，默认情况下可能被跳过。
"""

from __future__ import annotations

from collections.abc import Generator
from enum import Enum
from typing import TYPE_CHECKING, Any
from uuid import UUID

from lfx.interface.utils import extract_input_variables_from_prompt
from lfx.log.logger import logger
from lfx.schema.data import Data
from lfx.schema.message import Message

# 注意：`lfx` 需保持轻量，数据库相关依赖通过服务注入。
from lfx.services.deps import get_settings_service

if TYPE_CHECKING:
    from lfx.graph.vertex.base import Vertex


class UnbuiltObject:
    """标记未构建对象的占位类型。"""
    pass


class UnbuiltResult:
    """标记未构建结果的占位类型。"""
    pass


class ArtifactType(str, Enum):
    """产物类型枚举。"""
    TEXT = "text"
    RECORD = "record"
    OBJECT = "object"
    ARRAY = "array"
    STREAM = "stream"
    UNKNOWN = "unknown"
    MESSAGE = "message"


def validate_prompt(prompt: str):
    """校验提示词是否包含输入变量。

    契约：若提示词不含变量占位，则自动补齐 `{input}`。
    """
    if extract_input_variables_from_prompt(prompt):
        return prompt

    return fix_prompt(prompt)


def fix_prompt(prompt: str):
    """为提示词追加默认输入变量占位。"""
    return prompt + " {input}"


def flatten_list(list_of_lists: list[list | Any]) -> list:
    """扁平化二维列表为一维列表。"""
    new_list = []
    for item in list_of_lists:
        if isinstance(item, list):
            new_list.extend(item)
        else:
            new_list.append(item)
    return new_list


def get_artifact_type(value, build_result) -> str:
    """根据值类型判断产物类型。

    契约：返回 `ArtifactType` 字符串值。
    失败语义：未知类型默认返回 `unknown`。
    """
    result = ArtifactType.UNKNOWN
    match value:
        case Data():
            result = ArtifactType.RECORD

        case str():
            result = ArtifactType.TEXT

        case dict():
            result = ArtifactType.OBJECT

        case list():
            result = ArtifactType.ARRAY

        case Message():
            result = ArtifactType.MESSAGE

    if result == ArtifactType.UNKNOWN and (
        isinstance(build_result, Generator) or (isinstance(value, Message) and isinstance(value.text, Generator))
    ):
        result = ArtifactType.STREAM

    return result.value


def post_process_raw(raw, artifact_type: str):
    """对原始输出做最小化后处理。"""
    if artifact_type == ArtifactType.STREAM.value:
        raw = ""

    return raw


def _vertex_to_primitive_dict(target: Vertex) -> dict:
    """清洗节点参数，保留可序列化的基础类型。"""
    # 实现：过滤非基础类型，避免日志/存储失败。
    params = {
        key: value for key, value in target.params.items() if isinstance(value, str | int | bool | float | list | dict)
    }
    # 注意：列表元素也需是基础类型。
    for key, value in params.items():
        if isinstance(value, list):
            params[key] = [item for item in value if isinstance(item, str | int | bool | float | list | dict)]
    return params


async def log_transaction(
    flow_id: str | UUID,
    source: Vertex,
    status: str,
    target: Vertex | None = None,
    error: str | Exception | None = None,
    outputs: dict[str, Any] | None = None,
) -> None:
    """记录节点交易日志（可选持久化）。

    关键路径（三步）：
    1) 通过依赖注入获取交易服务
    2) 解析 `flow_id` 与输入/输出快照
    3) 异步写入交易记录
    失败语义：服务不可用时直接跳过，不影响主流程。
    """
    # 注意：日志服务不可用时直接跳过，避免影响主流程。
    try:
        # 注意：无源节点时直接返回。
        if source is None:
            return

        # 实现：通过依赖注入获取交易服务。
        from lfx.services.deps import get_transaction_service

        transaction_service = get_transaction_service()

        # 注意：服务不存在或未启用时直接跳过。
        if transaction_service is None or not transaction_service.is_enabled():
            return

        # 实现：解析 flow_id，优先使用源节点关联的 flow。
        if not flow_id:
            if source.graph.flow_id:
                flow_id = source.graph.flow_id
            else:
                return

        # 实现：将 UUID 统一转为字符串。
        flow_id_str = str(flow_id) if isinstance(flow_id, UUID) else flow_id

        # 实现：准备输入/输出快照。
        inputs = _vertex_to_primitive_dict(source) if source else None
        target_outputs = _vertex_to_primitive_dict(target) if target else None
        transaction_outputs = outputs if outputs is not None else target_outputs

        # 实现：异步写入交易日志。
        await transaction_service.log_transaction(
            flow_id=flow_id_str,
            vertex_id=source.id,
            inputs=inputs,
            outputs=transaction_outputs,
            status=status,
            target_id=target.id if target else None,
            error=str(error) if error else None,
        )

    except Exception as exc:  # noqa: BLE001
        logger.debug(f"Error logging transaction: {exc!s}")


async def log_vertex_build(
    *,
    flow_id: str | UUID,
    vertex_id: str,
    valid: bool,
    params: Any,
    data: dict | Any,
    artifacts: dict | None = None,
) -> None:
    """记录节点构建日志（可选持久化）。

    关键路径（三步）：
    1) 优先使用 langflow 数据库服务
    2) 序列化数据与产物
    3) 写入或降级为调试日志
    失败语义：无服务或未启用时跳过写入。
    """
    # 注意：该逻辑可选执行，不应影响主流程。
    try:
        # 实现：优先使用 langflow 的数据库服务。
        try:
            from langflow.services.deps import get_db_service as langflow_get_db_service
            from langflow.services.deps import get_settings_service as langflow_get_settings_service

            settings_service = langflow_get_settings_service()
            if not settings_service:
                return
            if not getattr(settings_service.settings, "vertex_builds_storage_enabled", False):
                return

            if isinstance(flow_id, str):
                flow_id = UUID(flow_id)

            from langflow.services.database.models.vertex_builds.crud import (
                log_vertex_build as crud_log_vertex_build,
            )
            from langflow.services.database.models.vertex_builds.model import VertexBuildBase

            # 实现：Pydantic 模型转 dict，保证可序列化。
            data_dict = data
            if hasattr(data, "model_dump"):
                data_dict = data.model_dump()
            elif hasattr(data, "dict"):
                data_dict = data.dict()

            # 实现：artifacts 同样进行序列化。
            artifacts_dict = artifacts
            if artifacts is not None:
                if hasattr(artifacts, "model_dump"):
                    artifacts_dict = artifacts.model_dump()
                elif hasattr(artifacts, "dict"):
                    artifacts_dict = artifacts.dict()

            vertex_build = VertexBuildBase(
                flow_id=flow_id,
                id=vertex_id,
                valid=valid,
                params=str(params) if params else None,
                data=data_dict,
                artifacts=artifacts_dict,
            )

            db_service = langflow_get_db_service()
            if db_service is None:
                return

            async with db_service._with_session() as session:  # noqa: SLF001
                await crud_log_vertex_build(session, vertex_build)

        except ImportError:
            # 注意：独立运行时仅做轻量日志。
            settings_service = get_settings_service()
            if not settings_service or not getattr(settings_service.settings, "vertex_builds_storage_enabled", False):
                return

            if isinstance(flow_id, str):
                flow_id = UUID(flow_id)

            # 实现：无数据库时记录调试日志。
            logger.debug(f"Vertex build logged: vertex={vertex_id}, flow={flow_id}, valid={valid}")

    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Error logging vertex build: {exc}")


def rewrite_file_path(file_path: str):
    """规范化文件路径并保留末两级路径。

    契约：返回长度为 1 的列表，便于与旧接口兼容。
    """
    file_path = file_path.replace("\\", "/")

    if ":" in file_path:
        file_path = file_path.split(":", 1)[-1]

    file_path_split = [part for part in file_path.split("/") if part]

    if len(file_path_split) > 1:
        consistent_file_path = f"{file_path_split[-2]}/{file_path_split[-1]}"
    else:
        consistent_file_path = "/".join(file_path_split)

    return [consistent_file_path]


def has_output_vertex(vertices: dict[Vertex, int]):
    """判断是否存在输出节点。"""
    return any(vertex.is_output for vertex in vertices)


def has_chat_output(vertices: dict[Vertex, int]):
    """判断是否存在 ChatOutput 组件节点。"""
    from lfx.graph.schema import InterfaceComponentTypes

    return any(InterfaceComponentTypes.ChatOutput in vertex.id for vertex in vertices)


def has_chat_input(vertices: dict[Vertex, int]):
    """判断是否存在 ChatInput 组件节点。"""
    from lfx.graph.schema import InterfaceComponentTypes

    return any(InterfaceComponentTypes.ChatInput in vertex.id for vertex in vertices)
