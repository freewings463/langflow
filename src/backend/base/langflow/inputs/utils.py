"""
模块名称：输入类型实例化工具

本模块提供运行时创建输入类型实例的轻量工具，主要用于从序列化数据恢复输入配置。
主要功能包括：
- 延迟加载 `InputTypesMap` 以避免循环依赖与启动成本。
- 根据 `input_type` 与数据字典实例化输入对象。

关键组件：`get_input_types_map`、`instantiate_input`。
设计背景：输入类型迁移至 `lfx` 后仍需保留旧路径实例化能力。
使用场景：`API`/配置反序列化输入定义时构造输入实例。
注意事项：未知 `input_type` 将抛 `ValueError`；`type` 会映射为 `field_type`。
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langflow.inputs.inputs import InputTypes, InputTypesMap
else:
    InputTypes = Any
    InputTypesMap = Any

# 注意：运行时延迟导入并缓存 `InputTypesMap`，避免循环依赖与冷启动开销。
_InputTypesMap: dict[str, type["InputTypes"]] | None = None


def get_input_types_map():
    """获取输入类型映射表（延迟导入）。

    契约：无输入，输出为 `InputTypesMap`；副作用：首次调用会触发导入并写入缓存。
    失败语义：导入失败时抛 `ImportError`，由调用方处理。
    """
    global _InputTypesMap  # noqa: PLW0603
    if _InputTypesMap is None:
        from langflow.inputs.inputs import InputTypesMap

        _InputTypesMap = InputTypesMap
    return _InputTypesMap


def instantiate_input(input_type: str, data: dict) -> InputTypes:
    """根据类型名与数据创建输入实例。

    契约：`input_type` 必须是 `InputTypesMap` 键，`data` 为字段字典；输出为输入实例。
    副作用：若 `data` 含 `type` 字段会就地改写为 `field_type` 以兼容旧格式。
    失败语义：未知类型抛 `ValueError`，由调用方决定降级或报错。
    """
    input_types_map = get_input_types_map()

    input_type_class = input_types_map.get(input_type)
    if "type" in data:
        # 注意：兼容旧字段名 `type`，避免覆盖内置属性。
        data["field_type"] = data.pop("type")
    if input_type_class:
        return input_type_class(**data)
    msg = f"Invalid input type: {input_type}"
    raise ValueError(msg)
