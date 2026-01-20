"""
模块名称：Data 条件路由组件

本模块提供基于 Data 字段值的条件路由能力，主要用于对 Data 或 Data 列表
进行条件判断并选择 True/False 分支输出。
主要功能包括：
- 支持字符串比较与布尔验证
- 支持单条 Data 与列表输入

关键组件：
- `DataConditionalRouterComponent`：Data 条件路由组件

设计背景：在数据流中提供对结构化字段的条件判断能力。
注意事项：组件已标记为 legacy，推荐使用替代组件。
"""

from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, DropdownInput, MessageTextInput, Output
from lfx.schema.data import Data
from lfx.schema.dotdict import dotdict


class DataConditionalRouterComponent(Component):
    """基于 Data 字段的条件路由组件。

    契约：`process_data` 返回符合条件的 Data 或列表；状态信息写入 `self.status`。
    失败语义：输入非法时返回包含错误信息的 `Data`。
    """
    display_name = "Condition"
    description = "Route Data object(s) based on a condition applied to a specified key, including boolean validation."
    icon = "split"
    name = "DataConditionalRouter"
    legacy = True
    replacement = ["logic.ConditionalRouter"]

    inputs = [
        DataInput(
            name="data_input",
            display_name="Data Input",
            info="The Data object or list of Data objects to process",
            is_list=True,
        ),
        MessageTextInput(
            name="key_name",
            display_name="Key Name",
            info="The name of the key in the Data object(s) to check",
        ),
        DropdownInput(
            name="operator",
            display_name="Operator",
            options=["equals", "not equals", "contains", "starts with", "ends with", "boolean validator"],
            info="The operator to apply for comparing the values. 'boolean validator' treats the value as a boolean.",
            value="equals",
        ),
        MessageTextInput(
            name="compare_value",
            display_name="Match Text",
            info="The value to compare against (not used for boolean validator)",
        ),
    ]

    outputs = [
        Output(display_name="True Output", name="true_output", method="process_data"),
        Output(display_name="False Output", name="false_output", method="process_data"),
    ]

    def compare_values(self, item_value: str, compare_value: str, operator: str) -> bool:
        """按操作符比较字符串值。"""
        if operator == "equals":
            return item_value == compare_value
        if operator == "not equals":
            return item_value != compare_value
        if operator == "contains":
            return compare_value in item_value
        if operator == "starts with":
            return item_value.startswith(compare_value)
        if operator == "ends with":
            return item_value.endswith(compare_value)
        if operator == "boolean validator":
            return self.parse_boolean(item_value)
        return False

    def parse_boolean(self, value):
        """解析布尔语义值，支持字符串与原生布尔。"""
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in {"true", "1", "yes", "y", "on"}
        return bool(value)

    def validate_input(self, data_item: Data) -> bool:
        """校验输入是否为 Data 且包含指定 key。"""
        if not isinstance(data_item, Data):
            self.status = "Input is not a Data object"
            return False
        if self.key_name not in data_item.data:
            self.status = f"Key '{self.key_name}' not found in Data"
            return False
        return True

    def process_data(self) -> Data | list[Data]:
        """处理单条或多条 Data 并按条件路由输出。

        关键路径（三步）：
        1) 判断输入为列表或单条
        2) 对每条 Data 进行条件判断
        3) 输出符合条件的分支结果
        异常流：输入非法时返回包含错误信息的 `Data`。
        """
        if isinstance(self.data_input, list):
            true_output = []
            false_output = []
            for item in self.data_input:
                if self.validate_input(item):
                    result = self.process_single_data(item)
                    if result:
                        true_output.append(item)
                    else:
                        false_output.append(item)
            self.stop("false_output" if true_output else "true_output")
            return true_output or false_output
        if not self.validate_input(self.data_input):
            return Data(data={"error": self.status})
        result = self.process_single_data(self.data_input)
        self.stop("false_output" if result else "true_output")
        return self.data_input

    def process_single_data(self, data_item: Data) -> bool:
        """处理单条 Data，返回条件是否成立。"""
        item_value = data_item.data[self.key_name]
        operator = self.operator

        if operator == "boolean validator":
            condition_met = self.parse_boolean(item_value)
            condition_description = f"Boolean validation of '{self.key_name}'"
        else:
            compare_value = self.compare_value
            condition_met = self.compare_values(str(item_value), compare_value, operator)
            condition_description = f"{self.key_name} {operator} {compare_value}"

        if condition_met:
            self.status = f"Condition met: {condition_description}"
            return True
        self.status = f"Condition not met: {condition_description}"
        return False

    def update_build_config(self, build_config: dotdict, field_value: Any, field_name: str | None = None):
        """根据操作符动态调整比较值字段的显示。"""
        if field_name == "operator":
            if field_value == "boolean validator":
                build_config["compare_value"]["show"] = False
                build_config["compare_value"]["advanced"] = True
                build_config["compare_value"]["value"] = None
            else:
                build_config["compare_value"]["show"] = True
                build_config["compare_value"]["advanced"] = False

        return build_config
