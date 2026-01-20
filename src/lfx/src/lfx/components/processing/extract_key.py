"""Data 键提取组件。

本模块从 Data 或 Data 列表中提取指定键，并返回对应值。
设计背景：旧组件保留以兼容历史流程。
注意事项：未找到键时会返回错误信息的 Data。
"""

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, Output, StrInput
from lfx.schema.data import Data


class ExtractDataKeyComponent(Component):
    """键提取组件封装。

    契约：输入为 Data 或 Data 列表与 `key`；输出为 Data 或 Data 列表。
    副作用：更新 `self.status`。
    失败语义：输入类型不符时返回错误 Data。
    """
    display_name = "Extract Key"
    description = (
        "Extract a specific key from a Data object or a list of "
        "Data objects and return the extracted value(s) as Data object(s)."
    )
    icon = "key"
    name = "ExtractaKey"
    legacy = True
    replacement = ["processing.DataOperations"]

    inputs = [
        DataInput(
            name="data_input",
            display_name="Data Input",
            info="The Data object or list of Data objects to extract the key from.",
        ),
        StrInput(
            name="key",
            display_name="Key to Extract",
            info="The key in the Data object(s) to extract.",
        ),
    ]

    outputs = [
        Output(display_name="Extracted Data", name="extracted_data", method="extract_key"),
    ]

    def extract_key(self) -> Data | list[Data]:
        """提取指定键并返回结果。"""
        key = self.key

        if isinstance(self.data_input, list):
            result = []
            for item in self.data_input:
                if isinstance(item, Data) and key in item.data:
                    extracted_value = item.data[key]
                    result.append(Data(data={key: extracted_value}))
            self.status = result
            return result
        if isinstance(self.data_input, Data):
            if key in self.data_input.data:
                extracted_value = self.data_input.data[key]
                result = Data(data={key: extracted_value})
                self.status = result
                return result
            self.status = f"Key '{key}' not found in Data object."
            return Data(data={"error": f"Key '{key}' not found in Data object."})
        self.status = "Invalid input. Expected Data object or list of Data objects."
        return Data(data={"error": "Invalid input. Expected Data object or list of Data objects."})
