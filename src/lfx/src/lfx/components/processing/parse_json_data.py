"""JSON 解析与筛选组件。

本模块将输入转换为 JSON，并通过 JQ 查询提取字段。
设计背景：旧组件保留以兼容历史流程。
注意事项：输入会通过 `json_repair` 修复后再解析。
"""

import json
from json import JSONDecodeError

import jq
from json_repair import repair_json

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import HandleInput, MessageTextInput
from lfx.io import Output
from lfx.log.logger import logger
from lfx.schema.data import Data
from lfx.schema.message import Message


class ParseJSONDataComponent(Component):
    """JSON 解析组件封装。

    契约：输入为 Message/Data 与 JQ 查询；输出为 Data 列表。
    副作用：无。
    失败语义：JSON 不合法抛 `ValueError`。
    """
    display_name = "Parse JSON"
    description = "Convert and extract JSON fields."
    icon = "braces"
    name = "ParseJSONData"
    legacy: bool = True
    replacement = ["processing.ParserComponent"]

    inputs = [
        HandleInput(
            name="input_value",
            display_name="Input",
            info="Data object to filter.",
            required=True,
            input_types=["Message", "Data"],
        ),
        MessageTextInput(
            name="query",
            display_name="JQ Query",
            info="JQ Query to filter the data. The input is always a JSON list.",
            required=True,
        ),
    ]

    outputs = [
        Output(display_name="Filtered Data", name="filtered_data", method="filter_data"),
    ]

    def _parse_data(self, input_value) -> str:
        """将输入值规范化为 JSON 字符串。"""
        if isinstance(input_value, Message) and isinstance(input_value.text, str):
            return input_value.text
        if isinstance(input_value, Data):
            return json.dumps(input_value.data)
        return str(input_value)

    def filter_data(self) -> list[Data]:
        """执行 JQ 过滤并返回结果列表。

        关键路径（三步）：
        1) 规范化输入并修复 JSON；
        2) 执行 JQ 查询；
        3) 将结果映射为 Data 列表。
        """
        to_filter = self.input_value
        if not to_filter:
            return []
        # 实现：兼容列表输入
        if isinstance(to_filter, list):
            to_filter = [self._parse_data(f) for f in to_filter]
        else:
            to_filter = self._parse_data(to_filter)

        # 实现：区分单对象与列表
        if not isinstance(to_filter, list):
            to_filter = repair_json(to_filter)
            try:
                to_filter_as_dict = json.loads(to_filter)
            except JSONDecodeError:
                try:
                    to_filter_as_dict = json.loads(repair_json(to_filter))
                except JSONDecodeError as e:
                    msg = f"Invalid JSON: {e}"
                    raise ValueError(msg) from e
        else:
            to_filter = [repair_json(f) for f in to_filter]
            to_filter_as_dict = []
            for f in to_filter:
                try:
                    to_filter_as_dict.append(json.loads(f))
                except JSONDecodeError:
                    try:
                        to_filter_as_dict.append(json.loads(repair_json(f)))
                    except JSONDecodeError as e:
                        msg = f"Invalid JSON: {e}"
                        raise ValueError(msg) from e
            to_filter = to_filter_as_dict

        full_filter_str = json.dumps(to_filter_as_dict)

        logger.info("to_filter: %s", to_filter)

        results = jq.compile(self.query).input_text(full_filter_str).all()
        logger.info("results: %s", results)
        return [Data(data=value) if isinstance(value, dict) else Data(text=str(value)) for value in results]
