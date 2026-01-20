"""
模块名称：图状态模型构建器

本模块提供基于组件方法的动态状态模型构建能力，主要用于在图执行中暴露组件输出。主要功能包括：
- 校验方法是否可用于输出绑定
- 为组件方法生成 getter/setter 以读写输出
- 动态生成 Pydantic 状态模型并支持 computed fields

关键组件：
- `__validate_method`：校验方法与组件约束
- `build_output_getter`：构建输出读取器
- `build_output_setter`：构建输出写入器
- `create_state_model`：动态创建状态模型

设计背景：图执行需要统一的状态对象来访问组件输出，且字段在运行时由组件集合决定。
使用场景：图运行时根据组件方法生成 `State` 模型并注入执行上下文。
注意事项：方法必须有返回类型注解；computed field 通过基类注入以满足 Pydantic 约束。
"""

from collections.abc import Callable
from typing import Any, get_type_hints

from pydantic import BaseModel, ConfigDict, computed_field, create_model
from pydantic.fields import FieldInfo


def __validate_method(method: Callable) -> None:
    """校验方法是否可用于输出绑定。

    契约：方法需为绑定实例的方法（存在 `__self__`），且所属类具备 `get_output_by_method`。
    失败语义：不满足约束时抛 `ValueError`。
    """
    if not hasattr(method, "__self__"):
        msg = f"Method {method} does not have a __self__ attribute."
        raise ValueError(msg)
    if not hasattr(method.__self__, "get_output_by_method"):
        msg = f"Method's class {method.__self__} must have a get_output_by_method attribute."
        raise ValueError(msg)


def build_output_getter(method: Callable, *, validate: bool = True) -> Callable:
    """为组件方法构建输出读取器。

    契约：方法必须包含返回类型注解；当 `validate=True` 时会校验方法归属。
    副作用：无。
    失败语义：缺少返回类型注解或校验失败抛 `ValueError`。
    """

    def output_getter(_):
        if validate:
            __validate_method(method)
        methods_class = method.__self__
        output = methods_class.get_output_by_method(method)
        return output.value

    return_type = get_type_hints(method).get("return", None)

    if return_type is None:
        msg = f"Method {method.__name__} has no return type annotation."
        raise ValueError(msg)
    output_getter.__annotations__["return"] = return_type
    return output_getter


def build_output_setter(method: Callable, *, validate: bool = True) -> Callable:
    """为组件方法构建输出写入器。

    契约：当 `validate=True` 时方法必须满足 `__validate_method` 约束。
    副作用：写入对应输出对象的 `value`。
    失败语义：校验失败抛 `ValueError`。
    """

    def output_setter(self, value) -> None:  # noqa: ARG001
        if validate:
            __validate_method(method)
        methods_class = method.__self__  # type: ignore[attr-defined]
        output = methods_class.get_output_by_method(method)
        output.value = value

    return output_setter


def create_state_model(model_name: str = "State", *, validate: bool = True, **kwargs) -> type:
    """创建动态 Pydantic 状态模型。

    契约：`kwargs` 可包含组件方法、`FieldInfo` 或 `(type, default)`；返回可实例化的模型类。
    副作用：无（仅构造类）。
    关键路径（三步）：1) 解析字段定义 2) 生成 computed fields 基类 3) 创建最终模型。
    失败语义：字段定义非法抛 `ValueError`/`TypeError`；方法缺少返回注解抛 `ValueError`。
    决策：通过 computed field + 基类组合构建可读写的状态字段。
    问题：需要把组件输出映射为可写属性且兼容 Pydantic 模型。
    方案：将方法包装为 getter/setter，并用 `computed_field` 注入基类。
    代价：动态类结构更复杂，调试成本增加。
    重评：当 Pydantic 提供更直接的可写 computed field 支持时重构。
    """
    fields = {}
    computed_fields_dict = {}

    for name, value in kwargs.items():
        if callable(value):
            try:
                __validate_method(value)
                getter = build_output_getter(value, validate=validate)
                setter = build_output_setter(value, validate=validate)
                property_method = property(getter, setter)
            except ValueError as e:
                # 注意：未通过组件方法校验时，允许将其视为已提供的 getter。
                if ("get_output_by_method" not in str(e) and "__self__" not in str(e)) or validate:
                    raise
                property_method = value
            # 实现：computed field 先挂到基类，避免 Pydantic 配置冲突。
            computed_fields_dict[name] = computed_field(property_method)
        elif isinstance(value, FieldInfo):
            field_tuple = (value.annotation or Any, value)
            fields[name] = field_tuple
        elif isinstance(value, tuple) and len(value) == 2:  # noqa: PLR2004
            # 注意：tuple 字段仅支持 (<type>, <default>) 或 `typing.Annotated` 形态。
            if not isinstance(value[0], type):
                msg = f"Invalid type for field {name}: {type(value[0])}"
                raise TypeError(msg)
            fields[name] = (value[0], value[1])
        else:
            msg = f"Invalid value type {type(value)} for field {name}"
            raise ValueError(msg)

    config_dict = ConfigDict(arbitrary_types_allowed=True, validate_assignment=True)

    if computed_fields_dict:
        # 实现：computed field 需先落到基类，再由最终模型继承。
        base_class_attrs = computed_fields_dict.copy()
        base_class_attrs["model_config"] = config_dict
        base_state_model = type(f"{model_name}Base", (BaseModel,), base_class_attrs)

        return create_model(model_name, __base__=base_state_model, __config__=config_dict, **fields)
    return create_model(model_name, __config__=config_dict, **fields)
