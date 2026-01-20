"""Data 转 DataFrame 组件。

本模块将一个或多个 Data 对象转换为 DataFrame 行。
主要功能包括：
- 将 `data` 字段展开为列
- 将 `text` 放入 `text` 列（如存在）

设计背景：旧组件保留以兼容历史流程。
注意事项：输入必须为 Data 或 Data 列表。
"""

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, Output
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame


class DataToDataFrameComponent(Component):
    """Data → DataFrame 组件封装。

    契约：输入为 Data 列表；输出为 DataFrame。
    副作用：更新 `self.status`。
    失败语义：输入包含非 Data 时抛 `TypeError`。
    """
    display_name = "Data → DataFrame"
    description = (
        "Converts one or multiple Data objects into a DataFrame. "
        "Each Data object corresponds to one row. Fields from `.data` become columns, "
        "and the `.text` (if present) is placed in a 'text' column."
    )
    icon = "table"
    name = "DataToDataFrame"
    legacy = True
    replacement = ["processing.DataOperations", "processing.TypeConverterComponent"]

    inputs = [
        DataInput(
            name="data_list",
            display_name="Data or Data List",
            info="One or multiple Data objects to transform into a DataFrame.",
            is_list=True,
        ),
    ]

    outputs = [
        Output(
            display_name="DataFrame",
            name="dataframe",
            method="build_dataframe",
            info="A DataFrame built from each Data object's fields plus a 'text' column.",
        ),
    ]

    def build_dataframe(self) -> DataFrame:
        """根据 Data 列表构建 DataFrame。

        关键路径（三步）：
        1) 规整输入为列表并校验类型；
        2) 展开 `data` 字段并补充 `text` 列；
        3) 构建 DataFrame 并写入状态。
        """
        data_input = self.data_list

        # 注意：兼容单个 Data 输入
        if not isinstance(data_input, list):
            data_input = [data_input]

        rows = []
        for item in data_input:
            if not isinstance(item, Data):
                msg = f"Expected Data objects, got {type(item)} instead."
                raise TypeError(msg)

            row_dict = dict(item.data) if item.data else {}

            text_val = item.get_text()
            if text_val:
                row_dict["text"] = text_val

            rows.append(row_dict)

        df_result = DataFrame(rows)
        self.status = df_result
        return df_result
