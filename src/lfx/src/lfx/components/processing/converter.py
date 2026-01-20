"""类型转换组件与辅助函数。

本模块在 Message/Data/DataFrame 之间进行类型转换，并可自动解析 JSON/CSV 文本。
主要功能包括：
- 文本结构化解析（JSON/CSV）
- 统一的类型转换入口
- 动态输出类型切换

注意事项：`auto_parse` 仅对文本类型生效，解析失败会回退为原始文本。
"""

import json
from typing import Any

from lfx.custom import Component
from lfx.io import BoolInput, HandleInput, Output, TabInput
from lfx.schema import Data, DataFrame, Message

MIN_CSV_LINES = 2


def convert_to_message(v) -> Message:
    """转换为 Message。

    契约：输入可为 Message/Data/DataFrame/dict；输出为 Message。
    失败语义：输入类型不支持时依赖 `to_message` 抛错。
    """
    return v if isinstance(v, Message) else v.to_message()


def convert_to_data(v: DataFrame | Data | Message | dict, *, auto_parse: bool) -> Data:
    """转换为 Data。

    契约：输入可为 Message/Data/DataFrame/dict；输出为 Data。
    副作用：当 `auto_parse=True` 时可能尝试解析 JSON/CSV。
    失败语义：输入类型不支持时依赖 `to_data` 抛错。
    """
    if isinstance(v, dict):
        return Data(v)
    if isinstance(v, Message):
        data = Data(data={"text": v.data["text"]})
        return parse_structured_data(data) if auto_parse else data

    return v if isinstance(v, Data) else v.to_data()


def convert_to_dataframe(v: DataFrame | Data | Message | dict, *, auto_parse: bool) -> DataFrame:
    """转换为 DataFrame。

    契约：输入可为 Message/Data/DataFrame/dict；输出为 DataFrame。
    失败语义：输入类型不支持时依赖 `to_dataframe` 抛错。
    """
    import pandas as pd

    if isinstance(v, dict):
        return DataFrame([v])
    if isinstance(v, DataFrame):
        return v
    # 实现：兼容 pandas.DataFrame
    if isinstance(v, pd.DataFrame):
        return DataFrame(data=v)

    if isinstance(v, Message):
        data = Data(data={"text": v.data["text"]})
        return parse_structured_data(data).to_dataframe() if auto_parse else data.to_dataframe()
    # 实现：其他类型调用自身的 to_dataframe
    return v.to_dataframe()


def parse_structured_data(data: Data) -> Data:
    """从 `Data.text` 中解析 JSON/CSV。

    契约：输入为 `Data`；输出为解析后的 `Data` 或原对象。
    失败语义：解析失败时返回原始 `Data`。
    """
    raw_text = data.get_text() or ""
    text = raw_text.lstrip("\ufeff").strip()

    # 实现：优先尝试 JSON
    parsed_json = _try_parse_json(text)
    if parsed_json is not None:
        return parsed_json

    # 实现：再尝试 CSV
    if _looks_like_csv(text):
        try:
            return _parse_csv_to_data(text)
        except Exception:  # noqa: BLE001
            # 注意：启发式误判或格式错误时保留原始数据
            return data

    return data


def _try_parse_json(text: str) -> Data | None:
    """尝试将文本解析为 JSON。"""
    try:
        parsed = json.loads(text)

        if isinstance(parsed, dict):
            return Data(data=parsed)
        if isinstance(parsed, list) and all(isinstance(item, dict) for item in parsed):
            return Data(data={"records": parsed})

    except (json.JSONDecodeError, ValueError):
        pass

    return None


def _looks_like_csv(text: str) -> bool:
    """基于简单启发式判断是否像 CSV。"""
    lines = text.strip().split("\n")
    if len(lines) < MIN_CSV_LINES:
        return False

    header_line = lines[0]
    return "," in header_line and len(lines) > 1


def _parse_csv_to_data(text: str) -> Data:
    """解析 CSV 文本并返回 Data。"""
    from io import StringIO

    import pandas as pd

    parsed_df = pd.read_csv(StringIO(text))
    records = parsed_df.to_dict(orient="records")

    return Data(data={"records": records})


class TypeConverterComponent(Component):
    """类型转换组件封装。

    契约：输入为 `Message`/`Data`/`DataFrame`；输出类型由 `output_type` 决定。
    副作用：动态更新输出端口并更新 `self.status`。
    失败语义：输入类型不支持时抛 `ValueError`/`TypeError`。
    """
    display_name = "Type Convert"
    description = "Convert between different types (Message, Data, DataFrame)"
    documentation: str = "https://docs.langflow.org/type-convert"
    icon = "repeat"

    inputs = [
        HandleInput(
            name="input_data",
            display_name="Input",
            input_types=["Message", "Data", "DataFrame"],
            info="Accept Message, Data or DataFrame as input",
            required=True,
        ),
        BoolInput(
            name="auto_parse",
            display_name="Auto Parse",
            info="Detect and convert JSON/CSV strings automatically.",
            advanced=True,
            value=False,
            required=False,
        ),
        TabInput(
            name="output_type",
            display_name="Output Type",
            options=["Message", "Data", "DataFrame"],
            info="Select the desired output data type",
            real_time_refresh=True,
            value="Message",
        ),
    ]

    outputs = [
        Output(
            display_name="Message Output",
            name="message_output",
            method="convert_to_message",
        )
    ]

    def update_outputs(self, frontend_node: dict, field_name: str, field_value: Any) -> dict:
        """根据输出类型动态刷新节点输出。"""
        if field_name == "output_type":
            frontend_node["outputs"] = []

            if field_value == "Message":
                frontend_node["outputs"].append(
                    Output(
                        display_name="Message Output",
                        name="message_output",
                        method="convert_to_message",
                    ).to_dict()
                )
            elif field_value == "Data":
                frontend_node["outputs"].append(
                    Output(
                        display_name="Data Output",
                        name="data_output",
                        method="convert_to_data",
                    ).to_dict()
                )
            elif field_value == "DataFrame":
                frontend_node["outputs"].append(
                    Output(
                        display_name="DataFrame Output",
                        name="dataframe_output",
                        method="convert_to_dataframe",
                    ).to_dict()
                )

        return frontend_node

    def convert_to_message(self) -> Message:
        """转换为 Message 并更新状态。"""
        input_value = self.input_data[0] if isinstance(self.input_data, list) else self.input_data

        # 实现：字符串先转为 Message
        if isinstance(input_value, str):
            input_value = Message(text=input_value)

        result = convert_to_message(input_value)
        self.status = result
        return result

    def convert_to_data(self) -> Data:
        """转换为 Data 并更新状态。"""
        input_value = self.input_data[0] if isinstance(self.input_data, list) else self.input_data

        # 实现：字符串先转为 Message
        if isinstance(input_value, str):
            input_value = Message(text=input_value)

        result = convert_to_data(input_value, auto_parse=self.auto_parse)
        self.status = result
        return result

    def convert_to_dataframe(self) -> DataFrame:
        """转换为 DataFrame 并更新状态。"""
        input_value = self.input_data[0] if isinstance(self.input_data, list) else self.input_data

        # 实现：字符串先转为 Message
        if isinstance(input_value, str):
            input_value = Message(text=input_value)

        result = convert_to_dataframe(input_value, auto_parse=self.auto_parse)
        self.status = result
        return result
