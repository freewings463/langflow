"""文本列表创建组件。

本模块将输入文本列表转换为 `Data` 列表，并可导出为 `DataFrame`。
设计背景：旧组件保留以兼容历史流程。
注意事项：每个输入文本会映射为一个 `Data(text=...)`。
"""

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import StrInput
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame
from lfx.template.field.base import Output


class CreateListComponent(Component):
    """文本列表创建组件封装。

    契约：输入为字符串列表；输出为 `Data` 列表或 `DataFrame`。
    副作用：更新 `self.status`。
    """
    display_name = "Create List"
    description = "Creates a list of texts."
    icon = "list"
    name = "CreateList"
    legacy = True

    inputs = [
        StrInput(
            name="texts",
            display_name="Texts",
            info="Enter one or more texts.",
            is_list=True,
        ),
    ]

    outputs = [
        Output(display_name="Data List", name="list", method="create_list"),
        Output(display_name="DataFrame", name="dataframe", method="as_dataframe"),
    ]

    def create_list(self) -> list[Data]:
        """将文本列表映射为 `Data` 列表。"""
        data = [Data(text=text) for text in self.texts]
        self.status = data
        return data

    def as_dataframe(self) -> DataFrame:
        """将 `Data` 列表转换为 DataFrame。"""
        return DataFrame(self.create_list())
