"""
模块名称：基础模型与动态 Schema 生成

本模块提供基础的 Pydantic 基类与运行时动态建模能力。
主要功能包括：
- 解析 schema 字段类型字符串为 Python 类型
- 基于字段描述动态生成 Pydantic 模型
- 将不同类型值收敛为布尔值

关键组件：
- `build_model_from_schema`
- `_get_type_annotation`
- `coalesce_bool`

设计背景：前端/配置以字符串描述类型，需要在运行时生成验证模型。
注意事项：类型映射表不在 schema 规范内时需同步更新。
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
    """将类型字符串映射为 Python 类型注解。

    契约：输入类型字符串与 `multiple`，返回对应类型或其列表类型。
    失败语义：未知类型抛 `ValueError`。

    决策：支持 `boolean`/`number` 等别名
    问题：上游 schema 使用的类型命名不统一
    方案：集中映射表统一转换
    代价：新类型需要维护映射表
    重评：当类型规范稳定后改为严格枚举
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
    """根据字段 schema 动态生成 Pydantic 模型。

    契约：输入字段列表，返回 `OutputModel` 模型类。
    关键路径（三步）：
    1) 解析字段类型与 `multiple`
    2) 生成 `Field` 描述信息
    3) 调用 `create_model` 构建模型
    失败语义：类型解析失败抛 `ValueError`。

    决策：字段描述写入 `Field(description=...)`
    问题：需要在 OpenAPI/表单中显示字段说明
    方案：统一注入 `description`
    代价：描述缺失时为空字符串
    重评：若引入 i18n 或富文本描述再调整
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
    """将不同类型的值收敛为布尔值。

    契约：支持 `bool`/`str`/`int`，其他类型返回 `False`。
    失败语义：不抛异常。

    决策：字符串真值采用 `TRUE_VALUES` 白名单
    问题：表单输入常以字符串传递
    方案：大小写不敏感匹配白名单
    代价：未列入白名单的字符串均视为 `False`
    重评：当需要支持更多真值表达时扩展列表
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in TRUE_VALUES
    if isinstance(value, int):
        return bool(value)
    return False
