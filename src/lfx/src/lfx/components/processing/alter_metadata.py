"""元数据增删组件。

本模块对输入的 Data/Message 批量追加或移除元数据字段，并支持导出 DataFrame。
主要功能包括：
- 规范化 metadata 字典并过滤空键
- 将文本输入转换为 Data 参与合并
- 批量移除指定字段

关键组件：
- AlterMetadataComponent：元数据增删入口

设计背景：旧组件保留以兼容历史流程。
注意事项：`remove_fields` 仅影响 `data` 字段，不修改 `text`。
"""

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import MessageTextInput
from lfx.io import HandleInput, NestedDictInput, Output, StrInput
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame


class AlterMetadataComponent(Component):
    """元数据增删组件封装。

    契约：输入为 Data/Message 列表与可选文本；输出 `Data` 列表或 `DataFrame`。
    副作用：更新 `self.status` 以便 UI/日志查看。
    失败语义：输入类型不匹配时抛 `TypeError`。
    """
    display_name = "Alter Metadata"
    description = "Adds/Removes Metadata Dictionary on inputs"
    icon = "merge"
    name = "AlterMetadata"
    legacy = True
    replacement = ["processing.DataOperations"]

    inputs = [
        HandleInput(
            name="input_value",
            display_name="Input",
            info="Object(s) to which Metadata should be added",
            required=False,
            input_types=["Message", "Data"],
            is_list=True,
        ),
        StrInput(
            name="text_in",
            display_name="User Text",
            info="Text input; value will be in 'text' attribute of Data object. Empty text entries are ignored.",
            required=False,
        ),
        NestedDictInput(
            name="metadata",
            display_name="Metadata",
            info="Metadata to add to each object",
            input_types=["Data"],
            required=True,
        ),
        MessageTextInput(
            name="remove_fields",
            display_name="Fields to Remove",
            info="Metadata Fields to Remove",
            required=False,
            is_list=True,
        ),
    ]

    outputs = [
        Output(
            name="data",
            display_name="Data",
            info="List of Input objects each with added Metadata",
            method="process_output",
        ),
        Output(
            display_name="DataFrame",
            name="dataframe",
            info="Data objects as a DataFrame, with metadata as columns",
            method="as_dataframe",
        ),
    ]

    def _as_clean_dict(self, obj):
        """将 Data/Dict 规整为干净的字典。

        契约：输入为 `Data` 或 `dict`；输出为去除空键的 `dict`。
        失败语义：类型不支持时抛 `TypeError`。
        """
        if isinstance(obj, dict):
            as_dict = obj
        elif isinstance(obj, Data):
            as_dict = obj.data
        else:
            msg = f"Expected a Data object or a dictionary but got {type(obj)}."
            raise TypeError(msg)

        return {k: v for k, v in (as_dict or {}).items() if k and k.strip()}

    def process_output(self) -> list[Data]:
        """合并输入并应用元数据增删规则。

        契约：输出为 `Data` 列表；输入为空时返回空列表。
        副作用：更新 `self.status`。
        关键路径（三步）：
        1) 规范化 metadata 并收集输入对象；
        2) 合并元数据并按需删除字段；
        3) 写入 `self.status` 并返回结果。
        """
        metadata = self._as_clean_dict(self.metadata)

        data_objects = [Data(text=self.text_in)] if self.text_in else []

        if self.input_value:
            data_objects.extend(self.input_value)

        # 实现：以新元数据覆盖同名键
        for data in data_objects:
            data.data.update(metadata)

        if self.remove_fields:
            fields_to_remove = {field.strip() for field in self.remove_fields if field.strip()}

            # 注意：仅删除 `data` 中字段，不触碰 `text`
            for data in data_objects:
                data.data = {k: v for k, v in data.data.items() if k not in fields_to_remove}

        self.status = data_objects
        return data_objects

    def as_dataframe(self) -> DataFrame:
        """将处理结果转换为 DataFrame。

        契约：输出为 `DataFrame`；每行对应一个 `Data`。
        副作用：触发 `process_output` 的计算与状态更新。
        """
        data_list = self.process_output()
        return DataFrame(data_list)
