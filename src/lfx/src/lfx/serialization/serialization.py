"""模块名称：通用序列化器

本模块提供统一的序列化入口与多类型分派逻辑，支持文本/集合截断与兼容多种对象类型。
主要功能包括：按类型分派序列化、处理 Pydantic/NumPy/Pandas、支持流式与不可序列化回退。

关键组件：
- `serialize`：统一序列化入口
- `_serialize_dispatcher`：类型分派器
- `UNSERIALIZABLE_SENTINEL`：不可序列化标记

设计背景：在日志、遥测与存储场景中需要稳定可控的序列化输出。
注意事项：`max_length/max_items` 控制截断，`to_str` 控制失败回退策略。
"""

from collections.abc import AsyncIterator, Generator, Iterator
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

import numpy as np
import pandas as pd
from langchain_core.documents import Document
from pydantic import BaseModel
from pydantic.v1 import BaseModel as BaseModelV1

from lfx.log.logger import logger
from lfx.serialization.constants import MAX_ITEMS_LENGTH, MAX_TEXT_LENGTH


def get_max_text_length() -> int:
    """获取序列化允许的最大文本长度。

    契约：输入无；输出最大长度整数；副作用无；失败语义：无。
    关键路径：1) 返回 `MAX_TEXT_LENGTH`。
    决策：集中通过常量控制
    问题：统一控制输出大小
    方案：常量集中管理
    代价：全局一刀切
    重评：当需要动态配置时改为配置项
    """
    return MAX_TEXT_LENGTH


def get_max_items_length() -> int:
    """获取序列化允许的最大元素数量。

    契约：输入无；输出最大数量整数；副作用无；失败语义：无。
    关键路径：1) 返回 `MAX_ITEMS_LENGTH`。
    决策：集中通过常量控制
    问题：统一控制集合大小
    方案：常量集中管理
    代价：全局一刀切
    重评：当需要动态配置时改为配置项
    """
    return MAX_ITEMS_LENGTH


# 用于标记序列化失败的哨兵对象。
# 采用独立类保证唯一性，并提供可读的 __repr__。
class _UnserializableSentinel:
    def __repr__(self):
        return "[Unserializable Object]"


UNSERIALIZABLE_SENTINEL = _UnserializableSentinel()


def _serialize_str(obj: str, max_length: int | None, _) -> str:
    """序列化字符串并按最大长度截断。

    契约：输入字符串与最大长度；输出字符串；副作用无；
    失败语义：无。
    关键路径：1) 判断长度 2) 截断并追加省略号。
    决策：超长时追加 `...`
    问题：避免日志/遥测过长
    方案：截断保留前缀
    代价：丢失尾部信息
    重评：当需要保留后缀时改为头尾截断
    """
    if max_length is None or len(obj) <= max_length:
        return obj
    return obj[:max_length] + "..."


def _serialize_bytes(obj: bytes, max_length: int | None, _) -> str:
    """将 bytes 解码为字符串并按需截断。

    契约：输入 bytes 与最大长度；输出字符串；副作用无；
    失败语义：解码异常将被忽略（errors="ignore"）。
    关键路径：1) 解码为 UTF-8 2) 可选截断。
    决策：忽略不可解码字节
    问题：日志需要可读输出
    方案：`errors="ignore"`
    代价：丢失不可解码内容
    重评：当需要保留原始字节时输出 base64
    """
    if max_length is not None:
        return (
            obj[:max_length].decode("utf-8", errors="ignore") + "..."
            if len(obj) > max_length
            else obj.decode("utf-8", errors="ignore")
        )
    return obj.decode("utf-8", errors="ignore")


def _serialize_datetime(obj: datetime, *_) -> str:
    """将 datetime 转为 UTC ISO 格式字符串。"""
    return obj.replace(tzinfo=timezone.utc).isoformat()


def _serialize_decimal(obj: Decimal, *_) -> float:
    """将 Decimal 转为 float。"""
    return float(obj)


def _serialize_uuid(obj: UUID, *_) -> str:
    """将 UUID 转为字符串。"""
    return str(obj)


def _serialize_document(obj: Document, max_length: int | None, max_items: int | None) -> Any:
    """序列化 LangChain Document（递归）。"""
    return serialize(obj.to_json(), max_length, max_items)


def _serialize_iterator(_: AsyncIterator | Generator | Iterator, *__) -> str:
    """统一处理未消费的迭代器。"""
    return "Unconsumed Stream"


def _serialize_pydantic(obj: BaseModel, max_length: int | None, max_items: int | None) -> Any:
    """处理 Pydantic v2 模型。"""
    serialized = obj.model_dump()
    return {k: serialize(v, max_length, max_items) for k, v in serialized.items()}


def _serialize_pydantic_v1(obj: BaseModelV1, max_length: int | None, max_items: int | None) -> Any:
    """兼容处理 Pydantic v1 模型。"""
    if hasattr(obj, "to_json"):
        return serialize(obj.to_json(), max_length, max_items)
    return serialize(obj.dict(), max_length, max_items)


def _serialize_dict(obj: dict, max_length: int | None, max_items: int | None) -> dict:
    """递归序列化字典值。"""
    return {k: serialize(v, max_length, max_items) for k, v in obj.items()}


def _serialize_list_tuple(obj: list | tuple, max_length: int | None, max_items: int | None) -> list:
    """截断过长列表并递归处理元素。"""
    if max_items is not None and len(obj) > max_items:
        truncated = list(obj)[:max_items]
        truncated.append(f"... [truncated {len(obj) - max_items} items]")
        obj = truncated
    return [serialize(item, max_length, max_items) for item in obj]


def _serialize_primitive(obj: Any, *_) -> Any:
    """处理原始类型（无需转换）。"""
    if obj is None or isinstance(obj, int | float | bool | complex):
        return obj
    return UNSERIALIZABLE_SENTINEL


def _serialize_instance(obj: Any, *_) -> str:
    """处理普通实例，转换为字符串。"""
    return str(obj)


def _truncate_value(value: Any, max_length: int | None, max_items: int | None) -> Any:
    """按类型与限制截断值。"""
    if max_length is not None and isinstance(value, str) and len(value) > max_length:
        return value[:max_length]
    if max_items is not None and isinstance(value, list | tuple) and len(value) > max_items:
        return value[:max_items]
    return value


def _serialize_dataframe(obj: pd.DataFrame, max_length: int | None, max_items: int | None) -> list[dict]:
    """将 DataFrame 序列化为记录列表。"""
    if max_items is not None and len(obj) > max_items:
        obj = obj.head(max_items)

    data = obj.to_dict(orient="records")

    return serialize(data, max_length, max_items)


def _serialize_series(obj: pd.Series, max_length: int | None, max_items: int | None) -> dict:
    """将 Series 序列化为字典。"""
    if max_items is not None and len(obj) > max_items:
        obj = obj.head(max_items)
    return {index: _truncate_value(value, max_length, max_items) for index, value in obj.items()}


def _is_numpy_type(obj: Any) -> bool:
    """判断对象是否为 NumPy 类型。"""
    return hasattr(type(obj), "__module__") and type(obj).__module__ == np.__name__


def _serialize_numpy_type(obj: Any, max_length: int | None, max_items: int | None) -> Any:
    """序列化 NumPy 类型。"""
    try:
        # 单元素数组
        if obj.size == 1 and hasattr(obj, "item"):
            return obj.item()

        # 多元素数组
        if np.issubdtype(obj.dtype, np.number):
            return obj.tolist()  # 转为 Python 列表
        if np.issubdtype(obj.dtype, np.bool_):
            return bool(obj)
        if np.issubdtype(obj.dtype, np.complexfloating):
            return complex(cast("complex", obj))
        if np.issubdtype(obj.dtype, np.str_):
            return _serialize_str(str(obj), max_length, max_items)
        if np.issubdtype(obj.dtype, np.bytes_) and hasattr(obj, "tobytes"):
            return _serialize_bytes(obj.tobytes(), max_length, max_items)
        if np.issubdtype(obj.dtype, np.object_) and hasattr(obj, "item"):
            return _serialize_instance(obj.item(), max_length, max_items)
    except Exception:  # noqa: BLE001
        return UNSERIALIZABLE_SENTINEL
    return UNSERIALIZABLE_SENTINEL


def _serialize_dispatcher(obj: Any, max_length: int | None, max_items: int | None) -> Any | _UnserializableSentinel:
    """根据类型分派到对应序列化器。"""
    # 先处理原始类型
    if obj is None:
        return obj
    primitive = _serialize_primitive(obj, max_length, max_items)
    if primitive is not UNSERIALIZABLE_SENTINEL:
        return primitive

    match obj:
        case str():
            return _serialize_str(obj, max_length, max_items)
        case bytes():
            return _serialize_bytes(obj, max_length, max_items)
        case datetime():
            return _serialize_datetime(obj, max_length, max_items)
        case Decimal():
            return _serialize_decimal(obj, max_length, max_items)
        case UUID():
            return _serialize_uuid(obj, max_length, max_items)
        case Document():
            return _serialize_document(obj, max_length, max_items)
        case AsyncIterator() | Generator() | Iterator():
            return _serialize_iterator(obj, max_length, max_items)
        case BaseModel():
            return _serialize_pydantic(obj, max_length, max_items)
        case BaseModelV1():
            return _serialize_pydantic_v1(obj, max_length, max_items)
        case dict():
            return _serialize_dict(obj, max_length, max_items)
        case pd.DataFrame():
            return _serialize_dataframe(obj, max_length, max_items)
        case pd.Series():
            return _serialize_series(obj, max_length, max_items)
        case list() | tuple():
            return _serialize_list_tuple(obj, max_length, max_items)
        case object() if _is_numpy_type(obj):
            return _serialize_numpy_type(obj, max_length, max_items)
        case object() if not isinstance(obj, type):  # 匹配非类实例
            return _serialize_instance(obj, max_length, max_items)
        case object() if hasattr(obj, "_name_"):  # Enum
            return f"{obj.__class__.__name__}.{obj._name_}"
        case object() if hasattr(obj, "__name__") and hasattr(obj, "__bound__"):  # TypeVar
            return repr(obj)
        case object() if hasattr(obj, "__origin__") or hasattr(obj, "__parameters__"):  # 类型别名/泛型
            return repr(obj)
        case _:
            # 处理 numpy 数值类型
            if hasattr(obj, "dtype"):
                if np.issubdtype(obj.dtype, np.number) and hasattr(obj, "item"):
                    return obj.item()
                if np.issubdtype(obj.dtype, np.bool_):
                    return bool(obj)
                if np.issubdtype(obj.dtype, np.complexfloating):
                    return complex(cast("complex", obj))
                if np.issubdtype(obj.dtype, np.str_):
                    return str(obj)
                if np.issubdtype(obj.dtype, np.bytes_) and hasattr(obj, "tobytes"):
                    return obj.tobytes().decode("utf-8", errors="ignore")
                if np.issubdtype(obj.dtype, np.object_) and hasattr(obj, "item"):
                    return serialize(obj.item())
            return UNSERIALIZABLE_SENTINEL


def serialize(
    obj: Any,
    max_length: int | None = None,
    max_items: int | None = None,
    *,
    to_str: bool = False,
) -> Any:
    """统一序列化入口（支持截断与失败回退）。

    契约：输入任意对象与限制参数；输出可序列化结果；
    副作用：可能记录调试日志；失败语义：异常时返回占位字符串或原对象。
    关键路径：1) 分派序列化 2) 处理类/泛型 3) 最终回退策略。
    决策：失败时仅在 `to_str=True` 才转字符串
    问题：避免误把不可序列化对象强转为字符串
    方案：按 `to_str` 控制回退
    代价：调用方需显式选择回退策略
    重评：当需要强制可序列化输出时默认启用回退
    """
    if obj is None:
        return None
    try:
        # 先尝试类型分派序列化
        result = _serialize_dispatcher(obj, max_length, max_items)
        if result is not UNSERIALIZABLE_SENTINEL:  # None 是合法结果，需要特殊判断
            return result

        # 处理类对象（含 Pydantic 类）
        if isinstance(obj, type):
            if issubclass(obj, BaseModel | BaseModelV1):
                return repr(obj)
            return str(obj)  # 处理其他类对象

        # 处理类型别名与泛型
        if hasattr(obj, "__origin__") or hasattr(obj, "__parameters__"):  # 类型别名/泛型
            try:
                return repr(obj)
            except Exception:  # noqa: BLE001
                logger.debug(f"Error serializing object: {obj}", exc_info=True)

        # 回退到常见序列化模式
        if hasattr(obj, "model_dump"):
            return serialize(obj.model_dump(), max_length, max_items)
        if hasattr(obj, "dict") and not isinstance(obj, type):
            return serialize(obj.dict(), max_length, max_items)

        # 最终回退：仅在 to_str=True 时转为字符串
        if to_str:
            return str(obj)

    except Exception:  # noqa: BLE001
        return "[Unserializable Object]"
    return obj


def serialize_or_str(
    obj: Any,
    max_length: int | None = MAX_TEXT_LENGTH,
    max_items: int | None = MAX_ITEMS_LENGTH,
) -> Any:
    """序列化失败则回退字符串表示。

    契约：输入对象与限制参数；输出可序列化结果或字符串；
    副作用：无；失败语义：无（始终返回可展示结果）。
    关键路径：1) 调用 `serialize` 并设置 `to_str=True`。
    决策：默认启用回退策略
    问题：需要确保可展示输出
    方案：强制 to_str=True
    代价：可能丢失结构化信息
    重评：当需要严格结构化时改为抛错
    """
    return serialize(obj, max_length, max_items, to_str=True)
