"""
模块名称：已停用组件集合

本模块提供已停用组件的集中导出入口，主要用于兼容旧版流程与迁移期引用。主要功能包括：
- 统一导出部分历史组件类，便于旧流程解析

关键组件：
- `ExtractKeyFromDataComponent` / `ListFlowsComponent` / `MergeDataComponent` 等

设计背景：在不暴露到新组件列表的前提下，保留旧流程的运行能力。
注意事项：此目录组件不再维护，接口可能在未来版本移除。
"""

from .extract_key_from_data import ExtractKeyFromDataComponent
from .list_flows import ListFlowsComponent
from .merge_data import MergeDataComponent
from .selective_passthrough import SelectivePassThroughComponent
from .split_text import SplitTextComponent
from .sub_flow import SubFlowComponent

__all__ = [
    "ExtractKeyFromDataComponent",
    "ListFlowsComponent",
    "MergeDataComponent",
    "SelectivePassThroughComponent",
    "SplitTextComponent",
    "SubFlowComponent",
]
