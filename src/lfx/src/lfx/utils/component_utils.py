"""模块名称：组件构建配置辅助

模块目的：集中处理组件 `build_config` 的增删改与显示控制。
主要功能：
- 字段增删改查与批量合并
- UI 展示/高级字段开关的统一处理
- 按动作选择字段集的可见性配置
使用场景：组件配置动态生成与前端字段显示联动。
关键组件：`set_field_display`、`set_field_advanced`、`merge_build_configs`
设计背景：`build_config` 为嵌套字典/`dotdict`，需要统一修改策略。
注意事项：多数函数会原地修改 `build_config`，调用方需避免共享引用副作用。
"""

from collections.abc import Callable
from typing import Any

from lfx.schema.dotdict import dotdict

DEFAULT_FIELDS = ["code", "_type"]


def update_fields(build_config: dotdict, fields: dict[str, Any]) -> dotdict:
    """按键更新已有字段（原地修改）。

    契约：仅更新 `build_config` 中已存在的键；不存在的键会被忽略。
    副作用：原地修改 `build_config`。
    """
    for key, value in fields.items():
        if key in build_config:
            build_config[key] = value
    return build_config


def add_fields(build_config: dotdict, fields: dict[str, Any]) -> dotdict:
    """追加字段到配置（原地修改）。

    契约：等价于 `dict.update`，会覆盖同名键。
    副作用：原地修改 `build_config`。
    """
    build_config.update(fields)
    return build_config


def delete_fields(build_config: dotdict, fields: dict[str, Any] | list[str]) -> dotdict:
    """删除指定字段（原地修改）。

    契约：支持传入字典或字段列表；不存在的键不会报错。
    副作用：原地修改 `build_config`。
    """
    if isinstance(fields, dict):
        fields = list(fields.keys())

    for field in fields:
        build_config.pop(field, None)
    return build_config


def get_fields(build_config: dotdict, fields: list[str] | None = None) -> dict[str, Any]:
    """读取指定字段并返回新字典。

    契约：`fields=None` 时返回全部字段的浅拷贝。
    副作用：无（不修改 `build_config`）。
    """
    if fields is None:
        return dict(build_config)

    result = {}
    for field in fields:
        if field in build_config:
            result[field] = build_config[field]
    return result


def update_input_types(build_config: dotdict) -> dotdict:
    """确保字段具备 `input_types` 列表（原地修改）。

    契约：对字典项补齐 `input_types=[]`；对对象属性补齐为空列表。
    副作用：原地修改 `build_config` 或字段对象。
    """
    for key, value in build_config.items():
        if isinstance(value, dict):
            if value.get("input_types") is None:
                build_config[key]["input_types"] = []
        elif hasattr(value, "input_types") and value.input_types is None:
            value.input_types = []
    return build_config


def set_field_display(build_config: dotdict, field: str, value: bool | None = None) -> dotdict:  # noqa: FBT001
    """设置字段是否在 UI 中展示（原地修改）。"""
    if field in build_config and isinstance(build_config[field], dict) and "show" in build_config[field]:
        build_config[field]["show"] = value
    return build_config


def set_multiple_field_display(
    build_config: dotdict,
    *,
    fields: dict[str, bool] | None = None,
    value: bool | None = None,
    field_list: list[str] | None = None,
) -> dotdict:
    """批量设置字段展示状态（原地修改）。

    契约：`fields` 以字段名->布尔值为准；`field_list` 则统一为 `value`。
    副作用：原地修改 `build_config`。
    """
    if fields is not None:
        for field, visibility in fields.items():
            build_config = set_field_display(build_config, field, value=visibility)
    elif field_list is not None:
        for field in field_list:
            build_config = set_field_display(build_config, field, value=value)
    return build_config


def set_field_advanced(build_config: dotdict, field: str, *, value: bool | None = None) -> dotdict:
    """设置字段是否为“高级项”（原地修改）。"""
    if value is None:
        value = False
    if field in build_config and isinstance(build_config[field], dict):
        build_config[field]["advanced"] = value
    return build_config


def set_multiple_field_advanced(
    build_config: dotdict,
    *,
    fields: dict[str, bool] | None = None,
    value: bool | None = None,
    field_list: list[str] | None = None,
) -> dotdict:
    """批量设置高级字段标记（原地修改）。"""
    if fields is not None:
        for field, advanced in fields.items():
            build_config = set_field_advanced(build_config, field, value=advanced)
    elif field_list is not None:
        for field in field_list:
            build_config = set_field_advanced(build_config, field, value=value)
    return build_config


def merge_build_configs(base_config: dotdict, override_config: dotdict) -> dotdict:
    """合并两个配置，`override_config` 优先生效。

    契约：对同名键且值为字典时做浅层递归合并，否则直接覆盖。
    副作用：返回新 `dotdict`，不修改入参。
    """
    result = dotdict(base_config.copy())
    for key, value in override_config.items():
        if key in result and isinstance(value, dict) and isinstance(result[key], dict):
            # 注意：仅合并一层嵌套键，避免深层结构被整体覆盖。
            for sub_key, sub_value in value.items():
                result[key][sub_key] = sub_value
        else:
            result[key] = value
    return result


def set_current_fields(
    build_config: dotdict,
    action_fields: dict[str, list[str]],
    *,
    selected_action: str | None = None,
    default_fields: list[str] = DEFAULT_FIELDS,
    func: Callable = set_field_display,
    default_value: bool | None = None,
) -> dotdict:
    """根据动作选择控制字段显示/高级状态（原地修改）。

    关键路径：
    1) 命中 `selected_action` 时启用对应字段
    2) 关闭其他动作字段
    3) 强制启用 `default_fields`

    契约：`selected_action` 命中时启用对应字段，并按 `default_value` 关闭其他动作字段。
    副作用：原地修改 `build_config`，并将 `default_fields` 置为启用。
    """
    # 注意：`action_fields` 形如 {action: [field1, field2]}。
    if default_value is None:
        default_value = False

    def _call_func(build_config: dotdict, field: str, *, value: bool) -> dotdict:
        """适配 `set_field_display` 与 `set_field_advanced` 的调用签名。"""
        if func == set_field_advanced:
            return func(build_config, field, value=value)
        return func(build_config, field, value)

    if selected_action in action_fields:
        for field in action_fields[selected_action]:
            build_config = _call_func(build_config, field, value=not default_value)
        for key, value in action_fields.items():
            if key != selected_action:
                for field in value:
                    build_config = _call_func(build_config, field, value=default_value)
    if selected_action is None:
        for value in action_fields.values():
            for field in value:
                build_config = _call_func(build_config, field, value=default_value)
    if default_fields is not None:
        for field in default_fields:
            build_config = _call_func(build_config, field, value=not default_value)
    return build_config
