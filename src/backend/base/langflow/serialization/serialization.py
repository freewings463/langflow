"""
模块名称：通用序列化工具

本模块负责将多种对象序列化为可记录/可传输的结构，并支持长度与数量截断。主要功能包括：
- 统一 `serialize` 入口与类型分发
- 兼容 `Pydantic` / `pandas` / `numpy` / `langchain` 文档等常见类型
- 提供 `MAX_TEXT_LENGTH` / `MAX_ITEMS_LENGTH` 限制与设置读取

关键组件：
- `serialize` / `serialize_or_str`: 对外序列化入口
- `_serialize_dispatcher`: 类型分发器

设计背景：日志与存储需要安全可控的序列化输出，并避免超长数据造成资源压力。
注意事项：失败时返回 `UNSERIALIZABLE_SENTINEL` 或占位字符串，具体由 `to_str` 决定。
"""

from collections.abc import AsyncIterator, Generator, Iterator
from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache
from typing import Any, cast
from uuid import UUID

import numpy as np
import pandas as pd
from langchain_core.documents import Document
from lfx.log.logger import logger
from pydantic import BaseModel
from pydantic.v1 import BaseModel as BaseModelV1

from langflow.serialization.constants import MAX_ITEMS_LENGTH, MAX_TEXT_LENGTH
from langflow.services.deps import get_settings_service


class _UnserializableSentinel:
    """无法序列化对象的哨兵类型，`__repr__` 输出固定占位文本。"""

    def __repr__(self):
        return "[Unserializable Object]"


# 注意：使用对象身份判断是否序列化失败，不应与字符串比较。
UNSERIALIZABLE_SENTINEL = _UnserializableSentinel()


@lru_cache(maxsize=1)
def get_max_text_length() -> int:
    """读取当前配置中的最大文本长度。

    契约：返回 `int`，来源于 `settings.max_text_length`；缓存后不会随运行期变更自动更新。
    副作用：使用 `lru_cache` 缓存结果。
    失败语义：设置服务不可用时抛异常。
    关键路径：1) 获取设置服务 2) 读取 `max_text_length`。
    决策：缓存设置值以降低重复依赖访问 问题：高频序列化会触发多次配置读取 方案：`lru_cache(maxsize=1)` 代价：热更新配置不即时生效 重评：当需要动态更新时改为按次读取。
    """
    return get_settings_service().settings.max_text_length


@lru_cache(maxsize=1)
def get_max_items_length() -> int:
    """读取当前配置中的集合最大长度限制。

    契约：返回 `int`，来源于 `settings.max_items_length`；结果被缓存。
    副作用：使用 `lru_cache` 缓存结果。
    失败语义：设置服务不可用时抛异常。
    关键路径：1) 获取设置服务 2) 读取 `max_items_length`。
    决策：统一从配置读取上限 问题：不同调用方可能自行传入上限导致不一致 方案：集中读取配置并缓存 代价：默认上限难以按场景细分 重评：当需要按场景区分时引入参数覆盖
    """
    return get_settings_service().settings.max_items_length


def _serialize_str(obj: str, max_length: int | None, _) -> str:
    """按 `max_length` 截断字符串并追加 `...`。"""
    if max_length is None or len(obj) <= max_length:
        return obj
    return obj[:max_length] + "..."


def _serialize_bytes(obj: bytes, max_length: int | None, _) -> str:
    """以 `utf-8` 忽略错误解码 `bytes`，并按 `max_length` 截断。"""
    if max_length is not None:
        return (
            obj[:max_length].decode("utf-8", errors="ignore") + "..."
            if len(obj) > max_length
            else obj.decode("utf-8", errors="ignore")
        )
    return obj.decode("utf-8", errors="ignore")


def _serialize_datetime(obj: datetime, *_) -> str:
    """将 `datetime` 统一为 `UTC` 的 `isoformat` 字符串。"""
    return obj.replace(tzinfo=timezone.utc).isoformat()


def _serialize_decimal(obj: Decimal, *_) -> float:
    """将 `Decimal` 转为 `float`，可能存在精度损失。"""
    return float(obj)


def _serialize_uuid(obj: UUID, *_) -> str:
    """将 `UUID` 转为标准字符串。"""
    return str(obj)


def _serialize_document(obj: Document, max_length: int | None, max_items: int | None) -> Any:
    """对 `Document.to_json()` 的结果递归 `serialize`。"""
    return serialize(obj.to_json(), max_length, max_items)


def _serialize_iterator(_: AsyncIterator | Generator | Iterator, *__) -> str:
    """对未消费迭代器返回固定占位文本，避免副作用。"""
    return "Unconsumed Stream"


def _serialize_pydantic(obj: BaseModel, max_length: int | None, max_items: int | None) -> Any:
    """使用 `model_dump()` 序列化 `Pydantic v2` 并递归字段。"""
    serialized = obj.model_dump()
    return {k: serialize(v, max_length, max_items) for k, v in serialized.items()}


def _serialize_pydantic_v1(obj: BaseModelV1, max_length: int | None, max_items: int | None) -> Any:
    """兼容 `Pydantic v1`：优先 `to_json()`，否则 `dict()`。"""
    if hasattr(obj, "to_json"):
        return serialize(obj.to_json(), max_length, max_items)
    return serialize(obj.dict(), max_length, max_items)


def _serialize_dict(obj: dict, max_length: int | None, max_items: int | None) -> dict:
    """仅递归处理字典值并保留原键。"""
    return {k: serialize(v, max_length, max_items) for k, v in obj.items()}


def _serialize_list_tuple(obj: list | tuple, max_length: int | None, max_items: int | None) -> list:
    """超过 `max_items` 时截断并追加 `... [truncated N items]` 标记。"""
    if max_items is not None and len(obj) > max_items:
        truncated = list(obj)[:max_items]
        truncated.append(f"... [truncated {len(obj) - max_items} items]")
        obj = truncated
    return [serialize(item, max_length, max_items) for item in obj]


def _serialize_primitive(obj: Any, *_) -> Any:
    """仅放行 `None` 与数值/布尔/复数，其他返回哨兵。"""
    if obj is None or isinstance(obj, int | float | bool | complex):
        return obj
    return UNSERIALIZABLE_SENTINEL


def _serialize_instance(obj: Any, *_) -> str:
    """对普通实例回退为 `str(obj)`。"""
    return str(obj)


def _truncate_value(value: Any, max_length: int | None, max_items: int | None) -> Any:
    """用于 `Series` 值的轻量截断，不追加省略标记。"""
    if max_length is not None and isinstance(value, str) and len(value) > max_length:
        return value[:max_length]
    if max_items is not None and isinstance(value, list | tuple) and len(value) > max_items:
        return value[:max_items]
    return value


def _serialize_dataframe(obj: pd.DataFrame, max_length: int | None, max_items: int | None) -> list[dict]:
    """对 `DataFrame` 取 `head` 并转 `records` 列表后递归。"""
    if max_items is not None and len(obj) > max_items:
        obj = obj.head(max_items)

    data = obj.to_dict(orient="records")

    return serialize(data, max_length, max_items)


def _serialize_series(obj: pd.Series, max_length: int | None, max_items: int | None) -> dict:
    """对 `Series` 取 `head` 并对值执行 `_truncate_value`。"""
    if max_items is not None and len(obj) > max_items:
        obj = obj.head(max_items)
    return {index: _truncate_value(value, max_length, max_items) for index, value in obj.items()}


def _is_numpy_type(obj: Any) -> bool:
    """通过 `type(obj).__module__` 判断是否为 `numpy` 类型。"""
    return hasattr(type(obj), "__module__") and type(obj).__module__ == np.__name__


def _serialize_numpy_type(obj: Any, max_length: int | None, max_items: int | None) -> Any:
    """序列化 `numpy` 标量与数组。

    契约：返回 Python 原生类型或可 JSON 化结构；失败返回 `UNSERIALIZABLE_SENTINEL`。
    副作用：无。
    失败语义：异常会记录 `debug` 日志并返回哨兵。
    关键路径（三步）：1) 单元素数组走 `item()` 2) 按 `dtype` 映射为列表/布尔/复数/字符串/字节 3) 对对象数组递归或转字符串。
    异常流：不支持的 `dtype` 或转换异常触发降级。
    性能瓶颈：`tolist()` 可能复制大数组。
    排障入口：日志关键字 `Cannot serialize numpy array`。
    决策：优先转为原生类型 问题：`numpy` 对象不便于日志与 JSON 方案：按 `dtype` 映射 代价：大数组转换带来内存开销 重评：当仅需摘要时改为采样输出
    """
    try:
        if obj.size == 1 and hasattr(obj, "item"):
            return obj.item()

        if np.issubdtype(obj.dtype, np.number):
            return obj.tolist()
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
    except Exception as e:  # noqa: BLE001
        logger.debug(f"Cannot serialize numpy array: {e!s}")
        return UNSERIALIZABLE_SENTINEL
    return UNSERIALIZABLE_SENTINEL


def _serialize_dispatcher(obj: Any, max_length: int | None, max_items: int | None) -> Any | _UnserializableSentinel:
    """将对象分发到对应的序列化器。

    契约：返回序列化结果或 `UNSERIALIZABLE_SENTINEL`；不保证结果可 JSON 化。
    副作用：无。
    失败语义：无法匹配类型时返回哨兵；下游异常向上抛出。
    关键路径（三步）：1) 先处理基础类型 2) 通过 `match` 分派专用序列化器 3) 处理 `numpy` 标量兜底。
    异常流：下游序列化器异常由 `serialize` 统一捕获。
    性能瓶颈：大量分支与递归调用。
    排障入口：确认对象类型是否在 `match` 分支中命中。
    决策：使用 `match` 实现类型分发 问题：多类型分发易导致分支混乱 方案：结构化模式匹配 代价：依赖 `Python 3.10+` 重评：当需要兼容旧版本时改为 `if/elif`
    """
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
        case object() if not isinstance(obj, type):
            return _serialize_instance(obj, max_length, max_items)
        case object() if hasattr(obj, "_name_"):
            return f"{obj.__class__.__name__}.{obj._name_}"
        case object() if hasattr(obj, "__name__") and hasattr(obj, "__bound__"):
            return repr(obj)
        case object() if hasattr(obj, "__origin__") or hasattr(obj, "__parameters__"):
            return repr(obj)
        case _:
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
    """统一序列化入口，支持长度与数量截断。

    契约：返回可序列化结构；`to_str=True` 时失败回退为字符串。
    副作用：记录 `logger.debug` 以便排障。
    失败语义：分发失败时返回原对象或占位字符串；异常时返回 `"[Unserializable Object]"`。
    关键路径（三步）：1) 调用 `_serialize_dispatcher` 2) 处理类/泛型等类型对象 3) 回退到 `model_dump`/`dict`/`str`。
    异常流：任何异常都会记录日志并返回占位字符串。
    性能瓶颈：递归序列化大型嵌套结构。
    排障入口：日志关键字 `Cannot serialize object`。
    决策：以哨兵而非抛错表示不可序列化 问题：序列化失败会中断调用链 方案：返回哨兵并在上层决定降级 代价：上游可能忽略失败信号 重评：当需要强一致性时改为抛异常
    """
    if obj is None:
        return None
    try:
        result = _serialize_dispatcher(obj, max_length, max_items)
        if result is not UNSERIALIZABLE_SENTINEL:
            return result

        if isinstance(obj, type):
            if issubclass(obj, BaseModel | BaseModelV1):
                return repr(obj)
            return str(obj)

        if hasattr(obj, "__origin__") or hasattr(obj, "__parameters__"):
            try:
                return repr(obj)
            except Exception as e:  # noqa: BLE001
                logger.debug(f"Cannot serialize object {obj}: {e!s}")

        if hasattr(obj, "model_dump"):
            return serialize(obj.model_dump(), max_length, max_items)
        if hasattr(obj, "dict") and not isinstance(obj, type):
            return serialize(obj.dict(), max_length, max_items)

        if to_str:
            return str(obj)

    except Exception as e:  # noqa: BLE001
        logger.debug(f"Cannot serialize object {obj}: {e!s}")
        return "[Unserializable Object]"
    return obj


def serialize_or_str(
    obj: Any,
    max_length: int | None = MAX_TEXT_LENGTH,
    max_items: int | None = MAX_ITEMS_LENGTH,
) -> Any:
    """带默认限制的序列化入口，失败时回退为字符串。

    契约：默认使用 `MAX_TEXT_LENGTH` / `MAX_ITEMS_LENGTH`；失败时返回 `str(obj)`。
    副作用：与 `serialize` 相同，可能记录 `debug` 日志。
    失败语义：无法序列化时返回字符串表示。
    关键路径：1) 注入默认上限 2) 调用 `serialize(..., to_str=True)` 3) 返回结果。
    决策：默认应用限制常量 问题：无限制序列化可能导致日志膨胀 方案：使用全局上限 代价：输出被截断 重评：当需要完整输出时允许上层显式传 `None`
    """
    return serialize(obj, max_length, max_items, to_str=True)
