"""动态更新 Data 组件。

本模块在现有 Data 上追加/更新字段，支持批量 Data 列表。
设计背景：旧组件保留以兼容历史流程。
注意事项：字段数量上限为 15。
"""

from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.field_typing.range_spec import RangeSpec
from lfx.inputs.inputs import (
    BoolInput,
    DataInput,
    DictInput,
    IntInput,
    MessageTextInput,
)
from lfx.io import Output
from lfx.schema.data import Data
from lfx.schema.dotdict import dotdict


class UpdateDataComponent(Component):
    """动态更新 Data 组件封装。

    契约：输入为旧 Data 与动态字段；输出为更新后的 Data 或列表。
    副作用：更新 `self.status`。
    失败语义：字段数超限或输入类型不符时抛 `ValueError`。
    """
    display_name: str = "Update Data"
    description: str = "Dynamically update or append data with the specified fields."
    name: str = "UpdateData"
    MAX_FIELDS = 15  # 最大字段数
    icon = "FolderSync"
    legacy = True
    replacement = ["processing.DataOperations"]

    inputs = [
        DataInput(
            name="old_data",
            display_name="Data",
            info="The record to update.",
            is_list=True,  # 支持 Data 列表输入
            required=True,
        ),
        IntInput(
            name="number_of_fields",
            display_name="Number of Fields",
            info="Number of fields to be added to the record.",
            real_time_refresh=True,
            value=0,
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

        关键路径（三步）：
        1) 解析字段数量并校验上限；
        2) 备份并重建动态字段；
        3) 回写 `number_of_fields` 并返回配置。
        """
        if field_name == "number_of_fields":
            default_keys = {
                "code",
                "_type",
                "number_of_fields",
                "text_key",
                "old_data",
                "text_key_validator",
            }
            try:
                field_value_int = int(field_value)
            except ValueError:
                return build_config

            if field_value_int > self.MAX_FIELDS:
                build_config["number_of_fields"]["value"] = self.MAX_FIELDS
                msg = f"Number of fields cannot exceed {self.MAX_FIELDS}. Try using a Component to combine two Data."
                raise ValueError(msg)

            existing_fields = {}
            # 实现：备份已生成的动态字段
            for key in list(build_config.keys()):
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

    async def build_data(self) -> Data | list[Data]:
        """合并旧数据与新字段并返回。

        关键路径（三步）：
        1) 汇总新字段；
        2) 逐条更新 Data 并校验 `text_key`；
        3) 写入状态并返回结果。
        """
        new_data = self.get_data()
        if isinstance(self.old_data, list):
            for data_item in self.old_data:
                if not isinstance(data_item, Data):
                    continue
                data_item.data.update(new_data)
                if self.text_key:
                    data_item.text_key = self.text_key
                self.validate_text_key(data_item)
            self.status = self.old_data
            return self.old_data
        if isinstance(self.old_data, Data):
            self.old_data.data.update(new_data)
            if self.text_key:
                self.old_data.text_key = self.text_key
            self.status = self.old_data
            self.validate_text_key(self.old_data)
            return self.old_data
        msg = "old_data is not a Data object or list of Data objects."
        raise ValueError(msg)

    def get_data(self):
        """从动态字段收集数据字典。"""
        data = {}
        default_keys = {
            "code",
            "_type",
            "number_of_fields",
            "text_key",
            "old_data",
            "text_key_validator",
        }
        for attr_name, attr_value in self._attributes.items():
            if attr_name in default_keys:
                continue
            if isinstance(attr_value, dict):
                for key, value in attr_value.items():
                    data[key] = value.get_text() if isinstance(value, Data) else value
            elif isinstance(attr_value, Data):
                data[attr_name] = attr_value.get_text()
            else:
                data[attr_name] = attr_value
        return data

    def validate_text_key(self, data: Data) -> None:
        """校验 `text_key` 是否存在于 Data 键集合中。"""
        data_keys = data.data.keys()
        if self.text_key and self.text_key not in data_keys:
            msg = f"Text Key: '{self.text_key}' not found in the Data keys: {', '.join(data_keys)}"
            raise ValueError(msg)
