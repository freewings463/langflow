"""动态创建 Data 组件。

本模块根据用户配置的字段数量动态生成输入，并输出一个 Data。
主要功能包括：
- 动态扩展输入字段
- 可选 `text_key` 及校验

注意事项：字段数量上限为 15，超出会被拒绝。
"""

from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.field_typing.range_spec import RangeSpec
from lfx.inputs.inputs import BoolInput, DictInput, IntInput, MessageTextInput
from lfx.io import Output
from lfx.schema.data import Data
from lfx.schema.dotdict import dotdict


class CreateDataComponent(Component):
    """动态创建 Data 的组件封装。

    契约：输入为动态字段与可选 `text_key`；输出为 `Data`。
    副作用：更新 `self.status`。
    失败语义：字段数超限抛 `ValueError`。
    """
    display_name: str = "Create Data"
    description: str = "Dynamically create a Data with a specified number of fields."
    name: str = "CreateData"
    MAX_FIELDS = 15  # 最大字段数
    legacy = True
    replacement = ["processing.DataOperations"]
    icon = "ListFilter"

    inputs = [
        IntInput(
            name="number_of_fields",
            display_name="Number of Fields",
            info="Number of fields to be added to the record.",
            real_time_refresh=True,
            value=1,
            range_spec=RangeSpec(min=1, max=MAX_FIELDS, step=1, step_type="int"),
        ),
        MessageTextInput(
            name="text_key",
            display_name="Text Key",
            info="Key that identifies the field to be used as the text content.",
            advanced=True,
        ),
        BoolInput(
            name="text_key_validator",
            display_name="Text Key Validator",
            advanced=True,
            info="If enabled, checks if the given 'Text Key' is present in the given 'Data'.",
        ),
    ]

    outputs = [
        Output(display_name="Data", name="data", method="build_data"),
    ]

    def update_build_config(self, build_config: dotdict, field_value: Any, field_name: str | None = None):
        """根据字段数量动态调整输入配置。

        契约：输入为 build_config 与字段值；输出更新后的 build_config。
        失败语义：字段数量超限抛 `ValueError`。
        关键路径（三步）：
        1) 解析字段数量并校验上限；
        2) 备份并重建动态字段；
        3) 回写 `number_of_fields` 并返回配置。
        """
        if field_name == "number_of_fields":
            default_keys = ["code", "_type", "number_of_fields", "text_key", "text_key_validator"]
            try:
                field_value_int = int(field_value)
            except ValueError:
                return build_config
            existing_fields = {}
            if field_value_int > self.MAX_FIELDS:
                build_config["number_of_fields"]["value"] = self.MAX_FIELDS
                msg = (
                    f"Number of fields cannot exceed {self.MAX_FIELDS}. "
                    "Please adjust the number of fields to be within the allowed limit."
                )
                raise ValueError(msg)
            if len(build_config) > len(default_keys):
                # 实现：备份已有动态字段
                for key in build_config.copy():
                    if key not in default_keys:
                        existing_fields[key] = build_config.pop(key)

            for i in range(1, field_value_int + 1):
                key = f"field_{i}_key"
                if key in existing_fields:
                    field = existing_fields[key]
                    build_config[key] = field
                else:
                    field = DictInput(
                        display_name=f"Field {i}",
                        name=key,
                        info=f"Key for field {i}.",
                        input_types=["Message", "Data"],
                    )
                    build_config[field.name] = field.to_dict()

            build_config["number_of_fields"]["value"] = field_value_int
        return build_config

    async def build_data(self) -> Data:
        """构建 Data 并写入 `text_key`。

        契约：输出为 `Data`。
        副作用：更新 `self.status`，可选校验 `text_key`。
        """
        data = self.get_data()
        return_data = Data(data=data, text_key=self.text_key)
        self.status = return_data
        if self.text_key_validator:
            self.validate_text_key()
        return return_data

    def get_data(self):
        """从动态字段收集数据字典。"""
        data = {}
        for value_dict in self._attributes.values():
            if isinstance(value_dict, dict):
                value_dict_ = {
                    key: value.get_text() if isinstance(value, Data) else value for key, value in value_dict.items()
                }
                data.update(value_dict_)
        return data

    def validate_text_key(self) -> None:
        """校验 `text_key` 是否存在于 Data 键集合中。"""
        data_keys = self.get_data().keys()
        if self.text_key not in data_keys and self.text_key != "":
            formatted_data_keys = ", ".join(data_keys)
            msg = f"Text Key: '{self.text_key}' not found in the Data keys: '{formatted_data_keys}'"
            raise ValueError(msg)
