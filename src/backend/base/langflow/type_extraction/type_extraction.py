"""
模块名称：type_extraction

本模块提供类型提取功能，主要用于从复杂的类型提示中提取内部类型信息。
主要功能包括：
- 从泛型别名中提取内部类型
- 从列表类型提示中提取内部类型
- 从联合类型提示中提取各组成类型

设计背景：在类型检查和动态类型处理过程中，需要从复合类型中提取基础类型信息
注意事项：使用时应注意处理各种泛型类型的兼容性
"""

import re
from collections.abc import Sequence as SequenceABC
from itertools import chain
from types import GenericAlias
from typing import Any, Union


def extract_inner_type_from_generic_alias(return_type: GenericAlias) -> Any:
    """从GenericAlias类型的类型提示中提取内部类型，特别处理列表或可选类型。
    
    关键路径（三步）：
    1) 检查类型是否为列表或SequenceABC类型
    2) 如果是，则提取其参数列表
    3) 否则返回原始类型
    
    异常流：对于非预期的类型可能会返回原始类型
    性能瓶颈：无显著性能瓶颈
    排障入口：检查返回类型是否符合预期
    """
    if return_type.__origin__ in {list, SequenceABC}:
        return list(return_type.__args__)
    return return_type


def extract_inner_type(return_type: str) -> str:
    """从字符串形式的类型提示中提取内部类型，特别处理列表类型。
    
    关键路径（三步）：
    1) 使用正则表达式匹配list[T]格式的类型
    2) 如果匹配成功，提取括号内的类型T
    3) 否则返回原始类型字符串
    
    异常流：对于非list格式的类型字符串返回原值
    性能瓶颈：正则匹配性能
    排障入口：检查返回的内部类型是否正确
    """
    if match := re.match(r"list\[(.*)\]", return_type, re.IGNORECASE):
        return match[1]
    return return_type


def extract_union_types(return_type: str) -> list[str]:
    """从字符串形式的类型提示中提取联合类型的所有组成类型。
    
    关键路径（三步）：
    1) 清理类型字符串，移除Union、方括号等标记
    2) 按逗号分割得到各个类型字符串
    3) 去除空白字符并返回类型列表
    
    异常流：对于格式不符合预期的联合类型可能产生错误结果
    性能瓶颈：字符串处理性能
    排障入口：检查返回的类型列表是否完整准确
    """
    # If the return type is a Union, then we need to parse it
    return_type = return_type.replace("Union", "").replace("[", "").replace("]", "")
    return_types = return_type.split(",")
    return [item.strip() for item in return_types]


def extract_uniont_types_from_generic_alias(return_type: GenericAlias) -> list:
    """从GenericAlias类型的类型提示中提取联合类型的所有组成类型。
    
    关键路径（三步）：
    1) 检查类型是否为列表类型
    2) 如果是列表，遍历每个类型并提取其参数，排除Any、None等特殊类型
    3) 否则直接返回类型参数列表
    
    异常流：对于非预期类型结构可能返回不完整结果
    性能瓶颈：嵌套循环处理大型联合类型
    排障入口：检查返回的类型列表是否排除了不需要的特殊类型
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
    """后处理函数返回类型，递归地提取和规范化类型信息。
    
    关键路径（三步）：
    1) 检查类型是否为列表或SequenceABC类型，如果是则提取内部类型
    2) 判断内部类型是否为Union类型，如果不是则直接返回
    3) 如果是Union类型，则递归处理每个子类型并去重
    
    异常流：对非标准类型结构可能导致异常或不完整处理
    性能瓶颈：递归处理深层嵌套类型结构
    排障入口：检查最终返回的类型列表是否符合预期格式
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
    """从GenericAlias类型的类型提示中提取联合类型的所有组成类型（修正版）。
    
    关键路径（三步）：
    1) 检查类型是否为列表类型
    2) 如果是列表，遍历每个类型并提取其参数，排除Any、None等特殊类型
    3) 否则直接返回类型参数列表
    
    异常流：对于非预期类型结构可能返回不完整结果
    性能瓶颈：嵌套循环处理大型联合类型
    排障入口：检查返回的类型列表是否排除了不需要的特殊类型
    """
    if isinstance(return_type, list):
        return [
            _inner_arg
            for _type in return_type
            for _inner_arg in _type.__args__
            if _inner_arg not in {Any, type(None), type(Any)}
        ]

    return list(return_type.__args__)
