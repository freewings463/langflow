"""构建产物（Artifact）类型与后处理工具。

本模块用于识别产物类型并对输出进行归一化处理。
"""

from collections.abc import Generator
from enum import Enum

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

from lfx.log.logger import logger
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame
from lfx.schema.encoders import CUSTOM_ENCODERS
from lfx.schema.message import Message
from lfx.serialization.serialization import serialize


class ArtifactType(str, Enum):
    """定义工件类型枚举。

    契约：
    - 输入：无
    - 输出：工件类型枚举实例
    - 副作用：无
    - 失败语义：无
    """
    TEXT = "text"
    DATA = "data"
    OBJECT = "object"
    ARRAY = "array"
    STREAM = "stream"
    UNKNOWN = "unknown"
    MESSAGE = "message"
    RECORD = "record"


def get_artifact_type(value, build_result=None) -> str:
    """获取值的工件类型。

    关键路径（三步）：
    1) 检查值的类型并匹配相应的处理逻辑
    2) 根据类型确定工件类型
    3) 返回工件类型的字符串表示

    异常流：无显式异常处理，使用默认类型。
    性能瓶颈：类型检查的匹配逻辑。
    排障入口：无特定日志输出。
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
    """将原始数据转换为字典列表。

    决策：将数据项转换为字典格式以确保兼容性。
    代价：增加了转换开销。
    """
    raw_ = []
    for item in raw:
        if hasattr(item, "dict") or hasattr(item, "model_dump"):
            raw_.append(serialize(item))
        else:
            raw_.append(str(item))
    return raw_


def post_process_raw(raw, artifact_type: str):
    """对原始数据进行后处理。

    关键路径（三步）：
    1) 根据工件类型确定处理逻辑
    2) 应用相应的转换或格式化
    3) 返回处理后的数据和类型

    异常流：捕获编码错误并记录调试日志。
    性能瓶颈：数据序列化和类型转换。
    排障入口：logger.debug 输出错误详情。
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
                # 排障：记录转换错误的详细信息
                logger.debug(f"Error converting to json: {raw} ({type(raw)})", exc_info=True)
                raw = default_message
        else:
            raw = default_message
    return raw, artifact_type
