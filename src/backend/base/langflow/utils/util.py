"""
模块名称：util

本模块提供各种实用函数，主要用于向后兼容。
主要功能包括：
- 从新的lfx.utils.util模块导入所有实用函数
- 确保旧版本API路径可用

设计背景：为了支持从旧版langflow到新版lfx的架构迁移，保持API兼容性
注意事项：新代码应直接使用 lfx.utils.util 模块
"""

from lfx.utils.util import (
    add_options_to_field,
    build_loader_repr_from_data,
    build_template_from_function,
    build_template_from_method,
    check_list_type,
    escape_json_dump,
    find_closest_match,
    format_dict,
    get_base_classes,
    get_default_factory,
    get_formatted_type,
    get_settings_service,
    get_type,
    get_type_from_union_literal,
    is_class_method,
    is_multiline_field,
    is_password_field,
    remove_ansi_escape_codes,
    remove_optional_wrapper,
    replace_default_value_with_actual,
    replace_mapping_with_dict,
    set_dict_file_attributes,
    set_headers_value,
    should_show_field,
    sync_to_async,
    unescape_string,
    update_settings,
    update_verbose,
)

__all__ = [
    "add_options_to_field",
    "build_loader_repr_from_data",
    "build_template_from_function",
    "build_template_from_method",
    "check_list_type",
    "escape_json_dump",
    "find_closest_match",
    "format_dict",
    "get_base_classes",
    "get_default_factory",
    "get_formatted_type",
    "get_settings_service",
    "get_type",
    "get_type_from_union_literal",
    "is_class_method",
    "is_multiline_field",
    "is_password_field",
    "remove_ansi_escape_codes",
    "remove_optional_wrapper",
    "replace_default_value_with_actual",
    "replace_mapping_with_dict",
    "set_dict_file_attributes",
    "set_headers_value",
    "should_show_field",
    "sync_to_async",
    "unescape_string",
    "update_settings",
    "update_verbose",
]
