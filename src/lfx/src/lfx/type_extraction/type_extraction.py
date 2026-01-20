"""
模块名称：lfx.type_extraction.type_extraction

本模块提供类型提示字符串与 `GenericAlias` 的解析工具，主要用于将复杂类型拆解为可用的内层类型集合。主要功能包括：
- 功能1：提取 list/Sequence/Optional 等内层类型
- 功能2：解析 Union/PEP604 类型并去重
- 功能3：对返回类型进行后处理以统一表示

关键组件：
- `extract_inner_type`：字符串类型提示解析
- `extract_union_types_from_generic_alias`：`GenericAlias` 的 Union 抽取
- `post_process_type`：返回类型标准化处理

设计背景：运行期需要将多种类型提示统一到可比较的类型集合。
注意事项：`GenericAlias` 依赖运行时类型信息；字符串解析仅覆盖基本格式。
"""

import re
from collections.abc import Sequence as SequenceABC
from itertools import chain
from types import GenericAlias
from typing import Any, Union


def extract_inner_type_from_generic_alias(return_type: GenericAlias) -> Any:
    """从 `GenericAlias` 中提取 list/Sequence 的内层类型。

    契约：若 `__origin__` 为 `list`/`Sequence` 返回 `__args__` 列表，否则原样返回。
    异常流：依赖传入对象具备 `__origin__`/`__args__`。
    """
    if return_type.__origin__ in {list, SequenceABC}:
        return list(return_type.__args__)
    return return_type


def extract_inner_type(return_type: str) -> str:
    """从字符串类型提示中提取 list 内层类型。

    契约：匹配 `list[...]` 返回括号内容；不匹配则原样返回。
    异常流：无显式异常。
    """
    if match := re.match(r"list\[(.*)\]", return_type, re.IGNORECASE):
        return match[1]
    return return_type


def extract_union_types(return_type: str) -> list[str]:
    """从字符串 Union 提示中拆分类型列表。

    契约：移除 `Union[]` 外壳并按逗号拆分。
    注意：不处理嵌套泛型中的逗号。
    """
    # If the return type is a Union, then we need to parse it
    return_type = return_type.replace("Union", "").replace("[", "").replace("]", "")
    return_types = return_type.split(",")
    return [item.strip() for item in return_types]


def extract_uniont_types_from_generic_alias(return_type: GenericAlias) -> list:
    """从 `GenericAlias` 中提取 Union 的内层类型列表。

    契约：过滤 `Any`/`NoneType` 并返回类型列表。
    注意：当传入 `list` 时，会扁平化其 `__args__`。
    """
    if isinstance(return_type, list):
        return [
            _inner_arg
            for _type in return_type
            for _inner_arg in _type.__args__
            if _inner_arg not in {Any, type(None), type(Any)}
        ]
    return list(return_type.__args__)


def post_process_type(type_):
    """标准化返回类型为可迭代集合。

    契约：若为 list/Sequence 则展开内层；若为 Union 则递归展开并去重。
    关键路径（三步）：1) 处理容器内层 2) 判断 Union 类型 3) 递归展开并去重。
    异常流：依赖传入类型结构，异常由 Python 类型系统抛出。
    """
    if hasattr(type_, "__origin__") and type_.__origin__ in {list, list, SequenceABC}:
        type_ = extract_inner_type_from_generic_alias(type_)

    # If the return type is not a Union, then we just return it as a list
    inner_type = type_[0] if isinstance(type_, list) else type_
    if (not hasattr(inner_type, "__origin__") or inner_type.__origin__ != Union) and (
        not hasattr(inner_type, "__class__") or inner_type.__class__.__name__ != "UnionType"
    ):
        return type_ if isinstance(type_, list) else [type_]
    # If the return type is a Union, then we need to parse it
    type_ = extract_union_types_from_generic_alias(type_)
    type_ = set(chain.from_iterable([post_process_type(t) for t in type_]))
    return list(type_)


def extract_union_types_from_generic_alias(return_type: GenericAlias) -> list:
    """从 `GenericAlias` 中提取 Union 的内层类型列表。

    契约：过滤 `Any`/`NoneType` 并返回类型列表。
    """
    if isinstance(return_type, list):
        return [
            _inner_arg
            for _type in return_type
            for _inner_arg in _type.__args__
            if _inner_arg not in {Any, type(None), type(Any)}
        ]
    return list(return_type.__args__)
