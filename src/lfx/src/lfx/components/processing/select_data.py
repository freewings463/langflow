"""Data 选择组件。

本模块从 Data 列表中按索引选择单个 Data。
设计背景：旧组件保留以兼容历史流程。
注意事项：索引越界会抛 `ValueError`。
"""

from lfx.custom.custom_component.component import Component
from lfx.field_typing.range_spec import RangeSpec
from lfx.inputs.inputs import DataInput, IntInput
from lfx.io import Output
from lfx.schema.data import Data


class SelectDataComponent(Component):
    """Data 选择组件封装。

    契约：输入为 Data 列表与索引；输出为单个 Data。
    副作用：更新 `self.status`。
    失败语义：索引越界抛 `ValueError`。
    """
    display_name: str = "Select Data"
    description: str = "Select a single data from a list of data."
    name: str = "SelectData"
    icon = "prototypes"
    legacy = True
    replacement = ["processing.DataOperations"]

    inputs = [
        DataInput(
            name="data_list",
            display_name="Data List",
            info="List of data to select from.",
            is_list=True,  # 该输入接受 Data 列表
        ),
        IntInput(
            name="data_index",
            display_name="Data Index",
            info="Index of the data to select.",
            value=0,  # 由外部动态更新范围
            range_spec=RangeSpec(min=0, max=15, step=1, step_type="int"),
        ),
    ]

    outputs = [
        Output(display_name="Selected Data", name="selected_data", method="select_data"),
    ]

    async def select_data(self) -> Data:
        """按索引返回 Data。"""
        selected_index = int(self.data_index)

        if selected_index < 0 or selected_index >= len(self.data_list):
            msg = f"Selected index {selected_index} is out of range."
            raise ValueError(msg)

        selected_data = self.data_list[selected_index]
        self.status = selected_data
        return selected_data
