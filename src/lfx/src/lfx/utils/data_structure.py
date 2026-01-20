"""模块名称：数据结构与类型推断

模块目的：从任意数据结构中提取“类型结构摘要”。
主要功能：
- 列表/字典结构的类型推断与样例抽取
- 递归深度控制与异常回退
- 针对 `Data` 对象的统一入口
使用场景：前端结构预览、调试与数据探索。
关键组件：`analyze_value`、`get_data_structure`
设计背景：需要结构化摘要而非完整数据传输。
注意事项：默认会截断递归深度并对异常进行字符串化回退。
"""

import json
from collections import Counter
from typing import Any

from lfx.schema.data import Data


def infer_list_type(items: list, max_samples: int = 5) -> str:
    """对列表元素做抽样类型推断。

    契约：最多抽样 `max_samples` 个元素；混合类型会用 `|` 连接。
    失败语义：空列表返回 `list(unknown)`。
    """
    if not items:
        return "list(unknown)"

    # 注意：避免全量遍历，减少大列表的成本。
    samples = items[:max_samples]
    types = [get_type_str(item) for item in samples]

    # 统计类型出现次数
    type_counter = Counter(types)

    if len(type_counter) == 1:
        # 单一类型
        return f"list({types[0]})"
    # 混合类型：显示所有已发现类型
    type_str = "|".join(sorted(type_counter.keys()))
    return f"list({type_str})"


def get_type_str(value: Any) -> str:
    """返回值的类型描述字符串（包含特判）。"""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        # 注意：字符串可能是日期/时间标识（启发式判断）
        if any(date_pattern in value.lower() for date_pattern in ["date", "time", "yyyy", "mm/dd", "dd/mm", "yyyy-mm"]):
            return "str(possible_date)"
        # 检测是否为 JSON 字符串
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
    # 自定义对象：返回类名
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
    """分析值的结构并返回类型描述/结构树。

    关键路径：
    1) 深度上限判断
    2) 处理 list/tuple/set/dict
    3) 回退为类型字符串或错误描述

    契约：递归深度超过 `max_depth` 时返回 `max_depth_reached(...)`。
    副作用：无。
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

            # 注意：列表元素为复杂结构时，附带首元素结构样例。
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
                    # 注意：结构分析失败时降级为错误字符串，避免整体中断。
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
    """将 `Data` 或字典转换为结构化的类型摘要。

    关键路径：
    1) 规范输入对象为字典
    2) 递归分析结构
    3) 按需附加样例值

    契约：返回字典包含 `structure`，可选 `samples`。
    副作用：无。
    """
    # 注意：统一兼容 `Data` 与普通字典输入。
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
    """递归提取样例值（用于展示，默认最多 3 个）。"""
    if isinstance(data, list | tuple | set):
        return [get_sample_values(item) for item in list(data)[:max_items]]
    if isinstance(data, dict):
        return {k: get_sample_values(v, max_items) for k, v in data.items()}
    return data
