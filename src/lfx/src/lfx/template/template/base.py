"""
模块名称：组件模板模型

本模块定义组件模板的数据结构与序列化逻辑，用于在系统中描述组件输入字段。
主要功能包括：
- 维护模板类型与字段集合
- 提供字段排序、序列化与反序列化逻辑
- 兼容历史格式的字段构造

关键组件：
- `Template`
- `from_dict`
- `serialize_model`

设计背景：组件模板需要稳定的序列化格式以便存储与跨模块传递。
注意事项：序列化时会将字段展开为顶层键值。
"""

from collections.abc import Callable
from typing import cast

from pydantic import BaseModel, Field, model_serializer

from lfx.inputs.inputs import InputTypes
from lfx.template.field.base import Input
from lfx.utils.constants import DIRECT_TYPES


class Template(BaseModel):
    """组件模板模型。

    契约：
    - 输入：模板类型名与字段列表
    - 输出：可序列化模板对象
    - 副作用：序列化时会展开字段为顶层键
    - 失败语义：字段反序列化失败时抛 `ValueError`
    """

    type_name: str = Field(serialization_alias="_type")
    fields: list[InputTypes]

    def process_fields(
        self,
        format_field_func: Callable | None = None,
    ) -> None:
        """对字段执行格式化回调（若提供）。"""
        if format_field_func:
            for field in self.fields:
                format_field_func(field, self.type_name)

    def sort_fields(self) -> None:
        """对字段排序：先按名称，再按直接类型优先。"""
        # 注意：先按名称排序，再将 DIRECT_TYPES 的字段置前
        self.fields.sort(key=lambda x: x.name or "")
        self.fields.sort(
            key=lambda x: x.field_type in DIRECT_TYPES if hasattr(x, "field_type") else False, reverse=False
        )

    @model_serializer(mode="wrap")
    def serialize_model(self, handler):
        """序列化模板，将字段展开为顶层键。"""
        result = handler(self)
        for field in self.fields:
            result[field.name] = field.model_dump(by_alias=True, exclude_none=True)

        return result

    @classmethod
    def from_dict(cls, data: dict) -> "Template":
        """从字典构建模板实例（兼容历史字段格式）。

        契约：
        - 输入：包含 `_type` 与字段键值的字典
        - 输出：`Template` 实例
        - 副作用：会就地修改传入字典内容
        - 失败语义：字段实例化失败时抛 `ValueError`
        """
        from lfx.inputs.inputs import instantiate_input

        for key, value in data.copy().items():
            if key == "_type":
                data["type_name"] = value
                del data[key]
            else:
                value["name"] = key
                if "fields" not in data:
                    data["fields"] = []
                input_type = value.pop("_input_type", None)
                if input_type:
                    try:
                        input_ = instantiate_input(input_type, value)
                    except Exception as e:
                        msg = f"Error instantiating input {input_type}: {e}"
                        raise ValueError(msg) from e
                else:
                    input_ = Input(**value)

                data["fields"].append(input_)

        # 注意：无输入字段时补空列表
        if "fields" not in data:
            data["fields"] = []

        return cls(**data)

    # 注意：兼容旧格式导出
    def to_dict(self, format_field_func=None):
        """导出模板为字典（兼容历史格式）。"""
        self.process_fields(format_field_func)
        self.sort_fields()
        return self.model_dump(by_alias=True, exclude_none=True, exclude={"fields"})

    def add_field(self, field: Input) -> None:
        """追加字段到模板。"""
        self.fields.append(field)

    def get_field(self, field_name: str) -> Input:
        """按字段名获取字段。

        失败语义：未找到时抛 `ValueError`。
        """
        field = next((field for field in self.fields if field.name == field_name), None)
        if field is None:
            msg = f"Field {field_name} not found in template {self.type_name}"
            raise ValueError(msg)
        return cast("Input", field)

    def update_field(self, field_name: str, field: Input) -> None:
        """更新指定字段。

        失败语义：未找到时抛 `ValueError`。
        """
        for idx, template_field in enumerate(self.fields):
            if template_field.name == field_name:
                self.fields[idx] = field
                return
        msg = f"Field {field_name} not found in template {self.type_name}"
        raise ValueError(msg)

    def upsert_field(self, field_name: str, field: Input) -> None:
        """更新字段；不存在时追加。"""
        try:
            self.update_field(field_name, field)
        except ValueError:
            self.add_field(field)
