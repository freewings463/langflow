"""
模块名称：Data 合并组件（已停用）

本模块提供将多个 `Data` 合并为统一字段集合的能力，主要用于在旧流程中对齐输出结构。主要功能包括：
- 汇总所有输入 `Data` 的键集合
- 对缺失键填充空字符串，输出字段一致的 `Data` 列表

关键组件：
- `MergeDataComponent`：合并组件

设计背景：避免下游组件因键缺失导致解析失败。
注意事项：输入必须为 `Data` 列表；缺失键将被填充空字符串。
"""

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, Output
from lfx.log.logger import logger
from lfx.schema.data import Data


class MergeDataComponent(Component):
    """Data 合并组件。

    契约：输入为 `Data` 列表，输出为字段一致的 `Data` 列表。
    失败语义：输入元素非 `Data` 时抛 `TypeError`。
    副作用：记录日志。
    """

    display_name = "Merge Data"
    description = (
        "Combines multiple Data objects into a unified list, ensuring all keys are present in each Data object."
    )
    icon = "merge"

    inputs = [
        DataInput(
            name="data_inputs",
            display_name="Data Inputs",
            is_list=True,
            info="A list of Data inputs objects to be merged.",
        ),
    ]

    outputs = [
        Output(
            display_name="Merged Data",
            name="merged_data",
            method="merge_data",
        ),
    ]

    def merge_data(self) -> list[Data]:
        """合并多个 `Data` 并补齐缺失字段。

        契约：输出列表中每个 `Data.data` 均包含所有输入键，缺失键填空字符串。
        失败语义：输入元素类型不正确时抛 `TypeError`；其他异常原样抛出。
        副作用：记录日志。

        关键路径（三步）：
        1) 校验输入并汇总所有键
        2) 为每个输入生成补齐字段的新 `Data`
        3) 返回合并后的列表
        """
        logger.info("Initiating the data merging process.")

        data_inputs: list[Data] = self.data_inputs
        logger.debug(f"Received {len(data_inputs)} data input(s) for merging.")

        if not data_inputs:
            logger.warning("No data inputs provided. Returning an empty list.")
            return []

        all_keys: set[str] = set()
        for idx, data_input in enumerate(data_inputs):
            if not isinstance(data_input, Data):
                error_message = f"Data input at index {idx} is not of type Data."
                logger.error(error_message)
                type_error_message = (
                    f"All items in data_inputs must be of type Data. Item at index {idx} is {type(data_input)}"
                )
                raise TypeError(type_error_message)
            all_keys.update(data_input.data.keys())
        logger.debug(f"Collected {len(all_keys)} unique key(s) from input data.")

        try:
            merged_data_list = []
            for idx, data_input in enumerate(data_inputs):
                merged_data_dict = {}

                for key in all_keys:
                    value = data_input.data.get(key, "")
                    if key not in data_input.data:
                        log_message = f"Key '{key}' missing in data input at index {idx}. Assigning empty string."
                        logger.debug(log_message)
                    merged_data_dict[key] = value

                merged_data = Data(
                    text_key=data_input.text_key, data=merged_data_dict, default_value=data_input.default_value
                )
                merged_data_list.append(merged_data)
                logger.debug(f"Merged Data object created for input at index {idx}.")

        except Exception:
            logger.exception("An error occurred during the data merging process.")
            raise

        logger.info("Data merging process completed successfully.")
        return merged_data_list
