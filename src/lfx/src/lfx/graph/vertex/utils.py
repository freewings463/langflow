"""
模块名称：Vertex 工具函数

模块目的：提供节点参数清洗与序列化辅助能力。
使用场景：将节点参数裁剪为可安全输出/持久化的基础类型。
主要功能包括：
- 过滤非基础类型参数，避免不可序列化对象

设计背景：前端或存储系统只接受基础类型参数。
注意：过滤会丢弃复杂对象，调用方需确认可接受。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lfx.graph.vertex.base import Vertex


def build_clean_params(target: Vertex) -> dict:
    """清洗节点参数为可序列化的基础类型。

    契约：输入 `Vertex`，输出仅包含基础类型的参数字典。
    异常流：不抛异常，无法序列化的值会被丢弃。
    性能：过滤成本与参数数量线性相关。
    排障：若字段缺失，检查参数是否为非基础类型。
    """
    params = {
        key: value for key, value in target.params.items() if isinstance(value, str | int | bool | float | list | dict)
    }
    for key, value in params.items():
        if isinstance(value, list):
            params[key] = [item for item in value if isinstance(item, str | int | bool | float | list | dict)]
    return params
