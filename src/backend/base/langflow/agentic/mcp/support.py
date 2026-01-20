"""
模块名称：`MCP` 结果归一化辅助

本模块提供 `MCP` 工具返回值的空值归一化与字段补齐，主要用于搜索结果的文本拼接与稳定输出。主要功能包括：
- 将 `None`/`"null"`/`NaN`/`NaT` 等占位值替换为统一标记
- 为缺失的必需字段补齐占位值

关键组件：
- `replace_none_and_null_with_empty_str`：列表字典批量清洗

设计背景：`LLM` 检索场景中空值会破坏拼接与排序，需统一占位
注意事项：非 `dict` 元素会原样返回；占位值为 `Not available`
"""

import math

from lfx.log.logger import logger


def replace_none_and_null_with_empty_str(data: list[dict], required_fields: list[str] | None = None) -> list[dict]:
    """归一化 `MCP` 搜索结果中的空值与缺失字段。

    契约：输入 `data`/`required_fields`；输出同长度列表；非 `dict` 元素原样返回；缺失字段补 `Not available`。
    关键路径：1) 逐项判断类型 2) 归一化 `None`/`null`/`NaN`/`NaT` 3) 补齐必需字段。
    失败语义：`math.isnan` 转换异常时记录 `aexception` 并继续。
    决策：占位值统一为 `Not available`
    问题：空字符串会与真实空值混淆并影响检索文本
    方案：用固定占位词替代空值/非法数值
    代价：调用方展示时需自行本地化
    重评：当 `UI`/检索需要可配置占位时
    """

    def convert_value(v):
        if v is None:
            return "Not available"
        if isinstance(v, str):
            v_stripped = v.strip().lower()
            if v_stripped in {"null", "nan", "infinity", "-infinity"}:
                return "Not available"
        if isinstance(v, float):
            try:
                if math.isnan(v):
                    return "Not available"
            except Exception as e:  # noqa: BLE001
                logger.aexception(f"Error converting value {v} to float: {e}")

        if hasattr(v, "isnat") and getattr(v, "isnat", False):
            return "Not available"
        return v

    not_avail = "Not available"
    required_fields_set = set(required_fields) if required_fields else set()
    result = []
    for d in data:
        if not isinstance(d, dict):
            result.append(d)
            continue
        new_dict = {k: convert_value(v) for k, v in d.items()}
        missing = required_fields_set - new_dict.keys()
        if missing:
            for k in missing:
                new_dict[k] = not_avail
        result.append(new_dict)
    return result
