"""
模块名称：lfx.type_extraction

本模块提供类型提示解析的统一入口，主要用于导出类型抽取相关工具函数。主要功能包括：
- 功能1：导出类型抽取与后处理函数

关键组件：
- `type_extraction`：类型解析实现

设计背景：集中管理类型解析工具，避免调用方散落实现细节。
注意事项：仅做导出聚合，不包含业务逻辑。
"""

from lfx.type_extraction.type_extraction import (
    extract_inner_type,
    extract_inner_type_from_generic_alias,
    extract_union_types,
    extract_union_types_from_generic_alias,
    extract_uniont_types_from_generic_alias,
    post_process_type,
)

__all__ = [
    "extract_inner_type",
    "extract_inner_type_from_generic_alias",
    "extract_union_types",
    "extract_union_types_from_generic_alias",
    "extract_uniont_types_from_generic_alias",
    "post_process_type",
]
