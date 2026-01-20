"""基础模型与 Schema 构建工具。

本模块提供从简化 schema 定义构建 Pydantic 模型的工具函数。
主要功能包括：
- 将字符串类型映射为 Python 类型
- 生成 Pydantic 模型与字段描述
- 将多种输入值归一为布尔值
"""

from typing import Any, TypedDict

from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, Field, create_model

TRUE_VALUES = ["true", "1", "t", "y", "yes"]


class SchemaField(TypedDict):
    name: str
    type: str
    description: str
    multiple: bool


class BaseModel(PydanticBaseModel):
    model_config = ConfigDict(populate_by_name=True)


def _get_type_annotation(type_str: str, *, multiple: bool) -> type:
    """将字符串类型映射为注解类型。

    契约：输入为类型字符串与是否多值；输出为 Python 类型。
    失败语义：未知类型抛 `ValueError`。
    """
    type_mapping = {
        "str": str,
        "int": int,
        "float": float,
        "bool": bool,
        "boolean": bool,
        "list": list[Any],
        "dict": dict[str, Any],
        "number": float,
        "text": str,
    }
    try:
        base_type = type_mapping[type_str]
    except KeyError as e:
        msg = f"Invalid type: {type_str}"
        raise ValueError(msg) from e
    if multiple:
        return list[base_type]  # type: ignore[valid-type]
    return base_type  # type: ignore[return-value]


def build_model_from_schema(schema: list[SchemaField]) -> type[PydanticBaseModel]:
    """根据 schema 构建 Pydantic 模型。

    关键路径（三步）：
    1) 解析字段类型与是否多值；
    2) 生成字段注解与描述；
    3) 创建并返回动态模型。
    """
    fields = {}
    for field in schema:
        field_name = field["name"]
        field_type_str = field["type"]
        description = field.get("description", "")
        multiple = field.get("multiple", False)
        multiple = coalesce_bool(multiple)
        field_type_annotation = _get_type_annotation(field_type_str, multiple=multiple)
        fields[field_name] = (field_type_annotation, Field(description=description))
    return create_model("OutputModel", **fields)


def coalesce_bool(value: Any) -> bool:
    """将输入归一为布尔值。

    契约：输入为任意类型；输出为 `bool`。
    失败语义：不抛异常，无法识别时返回 False。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in TRUE_VALUES
    if isinstance(value, int):
        return bool(value)
    return False
