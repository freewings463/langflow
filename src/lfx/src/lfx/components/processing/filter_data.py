"""Data 过滤组件（按键保留）。

本模块根据给定键列表过滤 Data 的 `data` 字段。
设计背景：旧组件保留以兼容历史流程。
注意事项：仅保留顶层键。
"""

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, MessageTextInput, Output
from lfx.schema.data import Data


class FilterDataComponent(Component):
    """Data 键过滤组件封装。

    契约：输入为 Data 与键列表；输出为过滤后的 Data。
    副作用：更新 `self.status`。
    """
    display_name = "Filter Data"
    description = "Filters a Data object based on a list of keys."
    icon = "filter"
    beta = True
    name = "FilterData"
    legacy = True
    replacement = ["processing.DataOperations"]

    inputs = [
        DataInput(
            name="data",
            display_name="Data",
            info="Data object to filter.",
        ),
        MessageTextInput(
            name="filter_criteria",
            display_name="Filter Criteria",
            info="List of keys to filter by.",
            is_list=True,
        ),
    ]

    outputs = [
        Output(display_name="Filtered Data", name="filtered_data", method="filter_data"),
    ]

    def filter_data(self) -> Data:
        """按键列表过滤 Data。"""
        filter_criteria: list[str] = self.filter_criteria
        data = self.data.data if isinstance(self.data, Data) else {}

        filtered = {key: value for key, value in data.items() if key in filter_criteria}

        filtered_data = Data(data=filtered)
        self.status = filtered_data
        return filtered_data
