"""
模块名称：data_structure

本模块提供数据结构分析和类型推断功能，主要用于理解和描述复杂数据的结构。
主要功能包括：
- 推断列表类型
- 获取值的类型字符串表示
- 分析值的结构
- 生成数据结构的详细schema表示

设计背景：在处理复杂数据结构时，需要一种方法来理解并描述数据的类型和结构
注意事项：处理深度嵌套结构时需要注意最大递归深度限制
"""

import json
from collections import Counter
from typing import Any

from langflow.schema.data import Data


def infer_list_type(items: list, max_samples: int = 5) -> str:
    """通过采样列表项来推断列表类型。
    
    关键路径（三步）：
    1) 从列表中采样最多max_samples个项目
    2) 获取每个样本的类型字符串表示
    3) 统计类型出现次数并返回合适的类型表示
    
    异常流：空列表返回'list(unknown)'
    性能瓶颈：大量样本的类型推断
    排障入口：检查返回的类型字符串是否正确反映了列表内容
    """
    if not items:
        return "list(unknown)"

    # Sample items (use all if less than max_samples)
    samples = items[:max_samples]
    types = [get_type_str(item) for item in samples]

    # Count type occurrences
    type_counter = Counter(types)

    if len(type_counter) == 1:
        # Single type
        return f"list({types[0]})"
    # Mixed types - show all found types
    type_str = "|".join(sorted(type_counter.keys()))
    return f"list({type_str})"


def get_type_str(value: Any) -> str:
    """获取值的详细类型字符串表示。
    
    关键路径（三步）：
    1) 检查基本类型（None, bool, int, float, str等）
    2) 对于字符串，进一步检查是否为日期或JSON
    3) 对于复杂类型（list, dict等），返回相应类型表示
    
    异常流：自定义对象返回类名
    性能瓶颈：JSON解析可能较慢
    排障入口：检查返回的类型字符串是否准确反映值的实际类型
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        # Check if string is actually a date/datetime
        if any(date_pattern in value.lower() for date_pattern in ["date", "time", "yyyy", "mm/dd", "dd/mm", "yyyy-mm"]):
            return "str(possible_date)"
        # Check if it's a JSON string
        try:
            json.loads(value)
            return "str(json)"
        except (json.JSONDecodeError, TypeError):
            pass
        else:
            return "str"
    if isinstance(value, list | tuple | set):
        return infer_list_type(list(value))
    if isinstance(value, dict):
        return "dict"
    # Handle custom objects
    return type(value).__name__


def analyze_value(
    value: Any,
    max_depth: int = 10,
    current_depth: int = 0,
    path: str = "",
    *,
    size_hints: bool = True,
    include_samples: bool = True,
) -> str | dict:
    """分析一个值并返回其结构及附加元数据。
    
    关键路径（三步）：
    1) 检查当前递归深度是否超过最大深度限制
    2) 根据值的类型（列表/元组/集合/字典/其他）分别处理
    3) 递归分析嵌套结构，收集类型和大小信息
    
    异常流：达到最大深度时返回'max_depth_reached'，发生异常时返回'error'
    性能瓶颈：深度嵌套结构的递归分析
    排障入口：检查返回的结构是否正确反映了输入值的类型和组织
    """
    if current_depth >= max_depth:
        return f"max_depth_reached(depth={max_depth})"

    try:
        if isinstance(value, list | tuple | set):
            length = len(value)
            if length == 0:
                return "list(unknown)"

            type_info = infer_list_type(list(value))
            size_info = f"[size={length}]" if size_hints else ""

            # For lists of complex objects, include a sample of the structure
            if (
                include_samples
                and length > 0
                and isinstance(value, list | tuple)
                and isinstance(value[0], dict | list)
                and current_depth < max_depth - 1
            ):
                sample = analyze_value(
                    value[0],
                    max_depth,
                    current_depth + 1,
                    f"{path}[0]",
                    size_hints=size_hints,
                    include_samples=include_samples,
                )
                return f"{type_info}{size_info}, sample: {json.dumps(sample)}"

            return f"{type_info}{size_info}"

        if isinstance(value, dict):
            result = {}
            for k, v in value.items():
                new_path = f"{path}.{k}" if path else k
                try:
                    result[k] = analyze_value(
                        v,
                        max_depth,
                        current_depth + 1,
                        new_path,
                        size_hints=size_hints,
                        include_samples=include_samples,
                    )
                except Exception as e:  # noqa: BLE001
                    result[k] = f"error({e!s})"
            return result

        return get_type_str(value)

    except Exception as e:  # noqa: BLE001
        return f"error({e!s})"


def get_data_structure(
    data_obj: Data | dict,
    max_depth: int = 10,
    max_sample_size: int = 3,
    *,
    size_hints: bool = True,
    include_sample_values: bool = False,
    include_sample_structure: bool = True,
) -> dict:
    """将Data对象或字典转换为详细的schema表示。
    
    关键路径（三步）：
    1) 处理Data对象或普通字典，提取实际数据
    2) 使用analyze_value函数分析数据结构
    3) 根据需要添加样本值
    
    异常流：无显式异常处理
    性能瓶颈：大数据集的递归分析
    排障入口：检查返回的结构是否正确反映了输入数据的组织方式
    """
    # Handle both Data objects and dictionaries
    data = data_obj.data if isinstance(data_obj, Data) else data_obj

    result = {
        "structure": analyze_value(
            data, max_depth=max_depth, size_hints=size_hints, include_samples=include_sample_structure
        )
    }

    if include_sample_values:
        result["samples"] = get_sample_values(data, max_items=max_sample_size)

    return result


def get_sample_values(data: Any, max_items: int = 3) -> Any:
    """从数据结构中获取样本值，处理嵌套结构。
    
    关键路径（三步）：
    1) 检查数据类型（列表/元组/集合/字典/其他）
    2) 对于集合类型，取前max_items个项目并递归处理
    3) 对于字典类型，递归处理每个键值对
    
    异常流：无异常处理
    性能瓶颈：深度嵌套结构的递归处理
    排障入口：检查返回的样本值是否代表了原始数据的结构
    """
    if isinstance(data, list | tuple | set):
        return [get_sample_values(item) for item in list(data)[:max_items]]
    if isinstance(data, dict):
        return {k: get_sample_values(v, max_items) for k, v in data.items()}
    return data
