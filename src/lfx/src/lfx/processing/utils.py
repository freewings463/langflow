"""
模块名称：lfx.processing.utils

本模块提供处理过程中的通用辅助工具，主要用于修复非标准 JSON 字符串。主要功能包括：
- 功能1：修复并解析不规范 JSON（`validate_and_repair_json`）

关键组件：
- `validate_and_repair_json`：JSON 修复入口

设计背景：运行期用户输入可能包含非严格 JSON，需在不抛异常的前提下尽可能恢复。
注意事项：修复失败时返回原字符串，不抛异常。
"""

import json
from typing import Any

from json_repair import repair_json


def validate_and_repair_json(json_str: str | dict) -> dict[str, Any] | str:
    """验证并尽量修复 JSON 字符串。

    契约：输入字符串或字典；若可修复则返回 dict，否则返回原字符串。
    关键路径（三步）：1) 非字符串直接返回 2) `repair_json` 修复 3) `json.loads` 解析。
    异常流：解析失败返回原字符串；不会抛异常给调用方。
    排障入口：关注调用方是否传入非 JSON 文本。
    """
    if not isinstance(json_str, str):
        return json_str
    try:
        repaired = repair_json(json_str)
        return json.loads(repaired)
    except (json.JSONDecodeError, ImportError):
        return json_str
