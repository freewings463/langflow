"""
模块名称：构建产物类型判定与后处理

本模块负责识别构建结果的产物类型并进行轻量后处理，主要用于日志与输出展示。主要功能包括：
- 依据运行结果推断 `ArtifactType`
- 将数组/对象/流式结果转换为可序列化结构

关键组件：
- ArtifactType：产物类型枚举
- get_artifact_type：类型识别入口
- post_process_raw：输出后处理

设计背景：构建结果来源多样，需要统一为前端可消费格式。
注意事项：流式产物以 `Generator` 识别；未知类型会降级为默认消息。
"""

from collections.abc import Generator
from enum import Enum

from fastapi.encoders import jsonable_encoder
from lfx.log.logger import logger
from pydantic import BaseModel

from langflow.schema.data import Data
from langflow.schema.dataframe import DataFrame
from langflow.schema.encoders import CUSTOM_ENCODERS
from langflow.schema.message import Message
from langflow.serialization.serialization import serialize


class ArtifactType(str, Enum):
    """构建产物类型枚举。"""

    TEXT = "text"
    DATA = "data"
    OBJECT = "object"
    ARRAY = "array"
    STREAM = "stream"
    UNKNOWN = "unknown"
    MESSAGE = "message"


def get_artifact_type(value, build_result=None) -> str:
    """推断构建产物类型。

    契约：返回 `ArtifactType` 的字符串值；`build_result` 可用于辅助判断流式产物。
    关键路径（三步）：
    1) 基于值的类型进行模式匹配。
    2) 处理 `Message`/`Data` 的嵌套内容。
    3) 根据 `Generator` 标记为流式类型。
    失败语义：无显式异常；未知类型返回 `unknown`。
    """
    result = ArtifactType.UNKNOWN
    match value:
        case Message():
            if not isinstance(value.text, str):
                enum_value = get_artifact_type(value.text)
                result = ArtifactType(enum_value)
            else:
                result = ArtifactType.MESSAGE
        case Data():
            enum_value = get_artifact_type(value.data)
            result = ArtifactType(enum_value)

        case str():
            result = ArtifactType.TEXT

        case dict():
            result = ArtifactType.OBJECT

        case list() | DataFrame():
            result = ArtifactType.ARRAY
    if result == ArtifactType.UNKNOWN and (
        (build_result and isinstance(build_result, Generator))
        or (isinstance(value, Message) and isinstance(value.text, Generator))
    ):
        result = ArtifactType.STREAM

    return result.value


def _to_list_of_dicts(raw):
    """将元素列表转换为可序列化字典列表。"""
    raw_ = []
    for item in raw:
        if hasattr(item, "dict") or hasattr(item, "model_dump"):
            raw_.append(serialize(item))
        else:
            raw_.append(str(item))
    return raw_


def post_process_raw(raw, artifact_type: str):
    """根据产物类型进行后处理。

    契约：返回 `(raw, artifact_type)`，其中 `raw` 为可序列化对象。
    副作用：无。
    失败语义：对象序列化失败时记录日志并回退默认消息。
    """
    default_message = "Built Successfully ✨"

    if artifact_type == ArtifactType.STREAM.value:
        raw = ""
    elif artifact_type == ArtifactType.ARRAY.value:
        raw = raw.to_dict(orient="records") if isinstance(raw, DataFrame) else _to_list_of_dicts(raw)
    elif artifact_type == ArtifactType.UNKNOWN.value and raw is not None:
        if isinstance(raw, BaseModel | dict):
            try:
                raw = jsonable_encoder(raw, custom_encoder=CUSTOM_ENCODERS)
                artifact_type = ArtifactType.OBJECT.value
            except Exception:  # noqa: BLE001
                logger.debug(f"Error converting to json: {raw} ({type(raw)})", exc_info=True)
                raw = default_message
        else:
            raw = default_message
    return raw, artifact_type
