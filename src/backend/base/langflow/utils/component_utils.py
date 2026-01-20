"""
模块名称：component_utils

本模块提供组件构建配置的实用函数，主要用于处理组件构建配置的字段操作。
主要功能包括：
- 更新、添加、删除和获取构建配置字段
- 设置字段显示和高级属性
- 合并构建配置

设计背景：在组件构建过程中，需要灵活地操作构建配置的各种字段属性
注意事项：使用时应确保传入的build_config是dotdict类型
"""

from collections.abc import Callable
from typing import Any

from langflow.schema.dotdict import dotdict

# 默认字段列表，包含代码和类型字段
DEFAULT_FIELDS = ["code", "_type"]


def update_fields(build_config: dotdict, fields: dict[str, Any]) -> dotdict:
    """更新build_config中指定字段的值。
    
    关键路径（三步）：
    1) 遍历要更新的字段字典
    2) 检查字段是否存在于build_config中
    3) 如果存在则更新其值
    
    异常流：不存在的字段会被跳过
    性能瓶颈：无显著性能瓶颈
    排障入口：检查返回的build_config是否包含期望的更新值
    """
    for key, value in fields.items():
        if key in build_config:
            build_config[key] = value
    return build_config


def add_fields(build_config: dotdict, fields: dict[str, Any]) -> dotdict:
    """向build_config添加新字段。
    
    关键路径（三步）：
    1) 获取要添加的字段字典
    2) 使用update方法添加所有字段
    3) 返回更新后的build_config
    
    异常流：无异常处理
    性能瓶颈：无显著性能瓶颈
    排障入口：检查返回的build_config是否包含新增的字段
    """
    build_config.update(fields)
    return build_config


def delete_fields(build_config: dotdict, fields: dict[str, Any] | list[str]) -> dotdict:
    """从build_config中删除指定字段。
    
    关键路径（三步）：
    1) 检查字段参数类型，如果是字典则提取键名
    2) 遍历要删除的字段列表
    3) 从build_config中移除字段
    
    异常流：不存在的字段会被忽略
    性能瓶颈：无显著性能瓶颈
    排障入口：检查返回的build_config是否不再包含删除的字段
    """
    if isinstance(fields, dict):
        fields = list(fields.keys())

    for field in fields:
        build_config.pop(field, None)
    return build_config


def get_fields(build_config: dotdict, fields: list[str] | None = None) -> dict[str, Any]:
    """从build_config中获取字段。如果fields为None，则返回所有字段。
    
    关键路径（三步）：
    1) 检查是否需要获取所有字段（fields为None）
    2) 如果指定了特定字段，则遍历这些字段
    3) 返回包含所需字段的字典
    
    异常流：不存在的字段会被跳过
    性能瓶颈：无显著性能瓶颈
    排障入口：检查返回的结果是否包含所有请求的字段
    """
    if fields is None:
        return dict(build_config)

    result = {}
    for field in fields:
        if field in build_config:
            result[field] = build_config[field]
    return result


def update_input_types(build_config: dotdict) -> dotdict:
    """更新build_config中所有字段的input_types。
    
    关键路径（三步）：
    1) 遍历build_config中的所有字段
    2) 检查字段值是否为字典或具有input_types属性
    3) 为没有input_types的字段初始化为空列表
    
    异常流：无异常处理
    性能瓶颈：无显著性能瓶颈
    排障入口：检查返回的build_config是否所有字段都有input_types属性
    """
    for key, value in build_config.items():
        if isinstance(value, dict):
            if value.get("input_types") is None:
                build_config[key]["input_types"] = []
        elif hasattr(value, "input_types") and value.input_types is None:
            value.input_types = []
    return build_config


def set_field_display(build_config: dotdict, field: str, value: bool | None = None) -> dotdict:  # noqa: FBT001
    """设置字段是否应在UI中显示。
    
    关键路径（三步）：
    1) 检查字段是否存在且为字典类型
    2) 确认字段有"show"键
    3) 设置show属性为指定值
    
    异常流：不存在的字段或非字典类型的字段会被跳过
    性能瓶颈：无显著性能瓶颈
    排障入口：检查返回的build_config中对应字段的show属性是否正确设置
    """
    if field in build_config and isinstance(build_config[field], dict) and "show" in build_config[field]:
        build_config[field]["show"] = value
    return build_config


def set_multiple_field_display(
    build_config: dotdict,
    fields: dict[str, bool] | None = None,
    *,
    value: bool | None = None,
    field_list: list[str] | None = None,
) -> dotdict:
    """一次性设置多个字段的显示属性。
    
    关键路径（三步）：
    1) 检查是否提供了字段字典或字段列表
    2) 根据参数类型分别处理字段设置
    3) 为每个字段调用set_field_display函数
    
    异常流：不存在的字段会在内部被跳过
    性能瓶颈：多次调用set_field_display函数
    排障入口：检查返回的build_config中对应字段的show属性是否正确设置
    """
    if fields is not None:
        for field, visibility in fields.items():
            build_config = set_field_display(build_config, field, value=visibility)
    elif field_list is not None:
        for field in field_list:
            build_config = set_field_display(build_config, field, value=value)
    return build_config


def set_field_advanced(build_config: dotdict, field: str, value: bool | None = None) -> dotdict:  # noqa: FBT001
    """设置字段在UI中是否被视为'高级'字段。
    
    关键路径（三步）：
    1) 检查value参数是否为None，若是则默认设为False
    2) 检查字段是否存在且为字典类型
    3) 设置advanced属性为指定值
    
    异常流：不存在的字段或非字典类型的字段会被跳过
    性能瓶颈：无显著性能瓶颈
    排障入口：检查返回的build_config中对应字段的advanced属性是否正确设置
    """
    if value is None:
        value = False
    if field in build_config and isinstance(build_config[field], dict):
        build_config[field]["advanced"] = value
    return build_config


def set_multiple_field_advanced(
    build_config: dotdict,
    fields: dict[str, bool] | None = None,
    *,
    value: bool | None = None,
    field_list: list[str] | None = None,
) -> dotdict:
    """一次性设置多个字段的高级属性。
    
    关键路径（三步）：
    1) 检查是否提供了字段字典或字段列表
    2) 根据参数类型分别处理字段设置
    3) 为每个字段调用set_field_advanced函数
    
    异常流：不存在的字段会在内部被跳过
    性能瓶颈：多次调用set_field_advanced函数
    排障入口：检查返回的build_config中对应字段的advanced属性是否正确设置
    """
    if fields is not None:
        for field, advanced in fields.items():
            build_config = set_field_advanced(build_config, field, value=advanced)
    elif field_list is not None:
        for field in field_list:
            build_config = set_field_advanced(build_config, field, value=value)
    return build_config


def merge_build_configs(base_config: dotdict, override_config: dotdict) -> dotdict:
    """合并两个构建配置，override_config具有更高优先级。
    
    关键路径（三步）：
    1) 创建基础配置的副本作为结果
    2) 遍历覆盖配置中的所有键值对
    3) 对于字典类型的值进行递归合并，其他类型直接覆盖
    
    异常流：无异常处理
    性能瓶颈：深度嵌套字典的复制和合并
    排障入口：检查返回的配置是否正确合并了所有字段
    """
    result = dotdict(base_config.copy())
    for key, value in override_config.items():
        if key in result and isinstance(value, dict) and isinstance(result[key], dict):
            # Recursively merge nested dictionaries
            for sub_key, sub_value in value.items():
                result[key][sub_key] = sub_value
        else:
            result[key] = value
    return result


def set_current_fields(
    build_config: dotdict,
    action_fields: dict[str, list[str]],
    selected_action: str | None = None,
    default_fields: list[str] = DEFAULT_FIELDS,
    func: Callable[[dotdict, str, bool], dotdict] = set_field_display,
    *,
    default_value: bool | None = None,
) -> dotdict:
    """为选定的操作设置当前字段。
    
    关键路径（三步）：
    1) 根据选定操作确定需要启用的字段
    2) 为其他操作关联的字段设置默认值
    3) 为默认字段设置相反的值
    
    异常流：未找到选定操作时会为所有操作关联字段设置默认值
    性能瓶颈：多次调用func函数
    排障入口：检查返回的build_config中字段的显示状态是否符合预期
    """
    # action_fields = {action1: [field1, field2], action2: [field3, field4]}
    # we need to show action of one field and disable the rest
    if default_value is None:
        default_value = False
    if selected_action in action_fields:
        for field in action_fields[selected_action]:
            build_config = func(build_config, field, not default_value)
        for key, value in action_fields.items():
            if key != selected_action:
                for field in value:
                    build_config = func(build_config, field, default_value)
    if selected_action is None:
        for value in action_fields.values():
            for field in value:
                build_config = func(build_config, field, default_value)
    if default_fields is not None:
        for field in default_fields:
            build_config = func(build_config, field, not default_value)
    return build_config
