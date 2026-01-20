"""数据操作组件。

本模块提供 Data 的选择、过滤、更新、路径提取与 JQ 查询等操作。
主要功能包括：
- 动态展示操作相关的输入字段
- 基于单一操作执行 Data 变换
- 兼容 JSON 修复与 JQ 查询

注意事项：一次仅支持一个操作；部分操作仅支持单条 Data 输入。
"""

import ast
import json
from typing import TYPE_CHECKING, Any

import jq
from json_repair import repair_json

from lfx.custom import Component
from lfx.inputs import DictInput, DropdownInput, MessageTextInput, SortableListInput
from lfx.io import DataInput, MultilineInput, Output
from lfx.log.logger import logger
from lfx.schema import Data
from lfx.schema.dotdict import dotdict
from lfx.utils.component_utils import set_current_fields, set_field_display

if TYPE_CHECKING:
    from collections.abc import Callable

ACTION_CONFIG = {
    "Select Keys": {"is_list": False, "log_msg": "setting filter fields"},
    "Literal Eval": {"is_list": False, "log_msg": "setting evaluate fields"},
    "Combine": {"is_list": True, "log_msg": "setting combine fields"},
    "Filter Values": {"is_list": False, "log_msg": "setting filter values fields"},
    "Append or Update": {"is_list": False, "log_msg": "setting Append or Update fields"},
    "Remove Keys": {"is_list": False, "log_msg": "setting remove keys fields"},
    "Rename Keys": {"is_list": False, "log_msg": "setting rename keys fields"},
    "Path Selection": {"is_list": False, "log_msg": "setting mapped key extractor fields"},
    "JQ Expression": {"is_list": False, "log_msg": "setting parse json fields"},
}
OPERATORS = {
    "equals": lambda a, b: str(a) == str(b),
    "not equals": lambda a, b: str(a) != str(b),
    "contains": lambda a, b: str(b) in str(a),
    "starts with": lambda a, b: str(a).startswith(str(b)),
    "ends with": lambda a, b: str(a).endswith(str(b)),
}


class DataOperationsComponent(Component):
    """Data 操作组件封装。

    契约：输入为 Data 列表与单一操作；输出为 `Data`。
    副作用：根据操作动态更新配置与日志输出。
    失败语义：非法组合/缺失字段时抛 `ValueError` 或 `TypeError`。
    """
    display_name = "Data Operations"
    description = "Perform various operations on a Data object."
    icon = "file-json"
    name = "DataOperations"
    default_keys = ["operations", "data"]
    metadata = {
        "keywords": [
            "data",
            "operations",
            "filter values",
            "Append or Update",
            "remove keys",
            "rename keys",
            "select keys",
            "literal eval",
            "combine",
            "filter",
            "append",
            "update",
            "remove",
            "rename",
            "data operations",
            "data manipulation",
            "data transformation",
            "data filtering",
            "data selection",
            "data combination",
            "Parse JSON",
            "JSON Query",
            "JQ Query",
        ],
    }
    actions_data = {
        "Select Keys": ["select_keys_input", "operations"],
        "Literal Eval": [],
        "Combine": [],
        "Filter Values": ["filter_values", "operations", "operator", "filter_key"],
        "Append or Update": ["append_update_data", "operations"],
        "Remove Keys": ["remove_keys_input", "operations"],
        "Rename Keys": ["rename_keys_input", "operations"],
        "Path Selection": ["mapped_json_display", "selected_key", "operations"],
        "JQ Expression": ["query", "operations"],
    }

    @staticmethod
    def extract_all_paths(obj, path=""):
        """提取 JSON 所有可访问路径。"""
        paths = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                new_path = f"{path}.{k}" if path else f".{k}"
                paths.append(new_path)
                paths.extend(DataOperationsComponent.extract_all_paths(v, new_path))
        elif isinstance(obj, list) and obj:
            new_path = f"{path}[0]"
            paths.append(new_path)
            paths.extend(DataOperationsComponent.extract_all_paths(obj[0], new_path))
        return paths

    @staticmethod
    def remove_keys_recursive(obj, keys_to_remove):
        """递归移除指定键。"""
        if isinstance(obj, dict):
            return {
                k: DataOperationsComponent.remove_keys_recursive(v, keys_to_remove)
                for k, v in obj.items()
                if k not in keys_to_remove
            }
        if isinstance(obj, list):
            return [DataOperationsComponent.remove_keys_recursive(item, keys_to_remove) for item in obj]
        return obj

    @staticmethod
    def rename_keys_recursive(obj, rename_map):
        """递归重命名键。"""
        if isinstance(obj, dict):
            return {
                rename_map.get(k, k): DataOperationsComponent.rename_keys_recursive(v, rename_map)
                for k, v in obj.items()
            }
        if isinstance(obj, list):
            return [DataOperationsComponent.rename_keys_recursive(item, rename_map) for item in obj]
        return obj

    inputs = [
        DataInput(name="data", display_name="Data", info="Data object to filter.", required=True, is_list=True),
        SortableListInput(
            name="operations",
            display_name="Operations",
            placeholder="Select Operation",
            info="List of operations to perform on the data.",
            options=[
                {"name": "Select Keys", "icon": "lasso-select"},
                {"name": "Literal Eval", "icon": "braces"},
                {"name": "Combine", "icon": "merge"},
                {"name": "Filter Values", "icon": "filter"},
                {"name": "Append or Update", "icon": "circle-plus"},
                {"name": "Remove Keys", "icon": "eraser"},
                {"name": "Rename Keys", "icon": "pencil-line"},
                {"name": "Path Selection", "icon": "mouse-pointer"},
                {"name": "JQ Expression", "icon": "terminal"},
            ],
            real_time_refresh=True,
            limit=1,
        ),
        # 选择键输入
        MessageTextInput(
            name="select_keys_input",
            display_name="Select Keys",
            info="List of keys to select from the data. Only top-level keys can be selected.",
            show=False,
            is_list=True,
        ),
        # 过滤值输入
        MessageTextInput(
            name="filter_key",
            display_name="Filter Key",
            info=(
                "Name of the key containing the list to filter. "
                "It must be a top-level key in the JSON and its value must be a list."
            ),
            is_list=True,
            show=False,
        ),
        DropdownInput(
            name="operator",
            display_name="Comparison Operator",
            options=["equals", "not equals", "contains", "starts with", "ends with"],
            info="The operator to apply for comparing the values.",
            value="equals",
            advanced=False,
            show=False,
        ),
        DictInput(
            name="filter_values",
            display_name="Filter Values",
            info="List of values to filter by.",
            show=False,
            is_list=True,
        ),
        # 追加/更新输入
        DictInput(
            name="append_update_data",
            display_name="Append or Update",
            info="Data to append or update the existing data with. Only top-level keys are checked.",
            show=False,
            value={"key": "value"},
            is_list=True,
        ),
        # 删除键输入
        MessageTextInput(
            name="remove_keys_input",
            display_name="Remove Keys",
            info="List of keys to remove from the data.",
            show=False,
            is_list=True,
        ),
        # 重命名键输入
        DictInput(
            name="rename_keys_input",
            display_name="Rename Keys",
            info="List of keys to rename in the data.",
            show=False,
            is_list=True,
            value={"old_key": "new_key"},
        ),
        MultilineInput(
            name="mapped_json_display",
            display_name="JSON to Map",
            info="Paste or preview your JSON here to explore its structure and select a path for extraction.",
            required=False,
            refresh_button=True,
            real_time_refresh=True,
            placeholder="Add a JSON example.",
            show=False,
        ),
        DropdownInput(
            name="selected_key", display_name="Select Path", options=[], required=False, dynamic=True, show=False
        ),
        MessageTextInput(
            name="query",
            display_name="JQ Expression",
            info="JSON Query to filter the data. Used by Parse JSON operation.",
            placeholder="e.g., .properties.id",
            show=False,
        ),
    ]
    outputs = [
        Output(display_name="Data", name="data_output", method="as_data"),
    ]

    # 数据操作辅助方法
    def get_data_dict(self) -> dict:
        """抽取 Data 的字典形式。"""
        data = self.data[0] if isinstance(self.data, list) and len(self.data) == 1 else self.data
        return data.model_dump()

    def json_query(self) -> Data:
        """执行 JQ 查询并返回结果 Data。

        契约：`query` 必填；输出为 `Data`。
        失败语义：JSON/查询错误抛 `ValueError`。
        关键路径（三步）：
        1) 序列化并修复 JSON；
        2) 执行 JQ 查询；
        3) 规范化结果并返回。
        """
        import json

        import jq

        if not self.query or not self.query.strip():
            msg = "JSON Query is required and cannot be blank."
            raise ValueError(msg)
        raw_data = self.get_data_dict()
        try:
            input_str = json.dumps(raw_data)
            repaired = repair_json(input_str)
            data_json = json.loads(repaired)
            jq_input = data_json["data"] if isinstance(data_json, dict) and "data" in data_json else data_json
            results = jq.compile(self.query).input(jq_input).all()
            if not results:
                msg = "No result from JSON query."
                raise ValueError(msg)
            result = results[0] if len(results) == 1 else results
            if result is None or result == "None":
                msg = "JSON query returned null/None. Check if the path exists in your data."
                raise ValueError(msg)
            if isinstance(result, dict):
                return Data(data=result)
            return Data(data={"result": result})
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as e:
            logger.error(f"JSON Query failed: {e}")
            msg = f"JSON Query error: {e}"
            raise ValueError(msg) from e

    def get_normalized_data(self) -> dict:
        """返回统一的数据字典（优先使用 `data` 字段）。"""
        data_dict = self.get_data_dict()
        return data_dict.get("data", data_dict)

    def data_is_list(self) -> bool:
        """判断是否为多条 Data 输入。"""
        return isinstance(self.data, list) and len(self.data) > 1

    def validate_single_data(self, operation: str) -> None:
        """确保操作仅作用于单条 Data。"""
        if self.data_is_list():
            msg = f"{operation} operation is not supported for multiple data objects."
            raise ValueError(msg)

    def operation_exception(self, operations: list[str]) -> None:
        """抛出不兼容操作组合异常。"""
        msg = f"{operations} operations are not supported in combination with each other."
        raise ValueError(msg)

    # 数据变换操作
    def select_keys(self, *, evaluate: bool | None = None) -> Data:
        """选择指定键并可选执行字面量解析。"""
        self.validate_single_data("Select Keys")
        data_dict = self.get_normalized_data()
        filter_criteria: list[str] = self.select_keys_input

        if len(filter_criteria) == 1 and filter_criteria[0] == "data":
            filtered = data_dict["data"]
        else:
            if not all(key in data_dict for key in filter_criteria):
                msg = f"Select key not found in data. Available keys: {list(data_dict.keys())}"
                raise ValueError(msg)
            filtered = {key: value for key, value in data_dict.items() if key in filter_criteria}

        if evaluate:
            filtered = self.recursive_eval(filtered)

        return Data(data=filtered)

    def remove_keys(self) -> Data:
        """递归移除指定键。"""
        self.validate_single_data("Remove Keys")
        data_dict = self.get_normalized_data()
        remove_keys_input: list[str] = self.remove_keys_input

        filtered = DataOperationsComponent.remove_keys_recursive(data_dict, set(remove_keys_input))
        return Data(data=filtered)

    def rename_keys(self) -> Data:
        """递归重命名指定键。"""
        self.validate_single_data("Rename Keys")
        data_dict = self.get_normalized_data()
        rename_keys_input: dict[str, str] = self.rename_keys_input

        renamed = DataOperationsComponent.rename_keys_recursive(data_dict, rename_keys_input)
        return Data(data=renamed)

    def recursive_eval(self, data: Any) -> Any:
        """递归解析可被字面量求值的字符串。"""
        if isinstance(data, dict):
            return {k: self.recursive_eval(v) for k, v in data.items()}
        if isinstance(data, list):
            return [self.recursive_eval(item) for item in data]
        if isinstance(data, str):
            try:
                # 注意：仅尝试解析可能是字面量的字符串
                if (
                    data.strip().startswith(("{", "[", "(", "'", '"'))
                    or data.strip().lower() in ("true", "false", "none")
                    or data.strip().replace(".", "").isdigit()
                ):
                    return ast.literal_eval(data)
            except (ValueError, SyntaxError, TypeError, MemoryError):
                return data
            else:
                return data
        return data

    def evaluate_data(self) -> Data:
        """对 Data 中字符串进行字面量解析。"""
        self.validate_single_data("Literal Eval")
        logger.info("evaluating data")
        return Data(**self.recursive_eval(self.get_data_dict()))

    def combine_data(self, *, evaluate: bool | None = None) -> Data:
        """合并多条 Data 为一条。

        关键路径（三步）：
        1) 校验输入数量；
        2) 按键合并并处理列表/非列表冲突；
        3) 可选执行字面量解析并返回。
        """
        logger.info("combining data")
        if not self.data_is_list():
            return self.data[0] if self.data else Data(data={})

        if len(self.data) == 1:
            msg = "Combine operation requires multiple data inputs."
            raise ValueError(msg)

        data_dicts = [data.model_dump().get("data", data.model_dump()) for data in self.data]
        combined_data = {}

        for data_dict in data_dicts:
            for key, value in data_dict.items():
                if key not in combined_data:
                    combined_data[key] = value
                elif isinstance(combined_data[key], list):
                    if isinstance(value, list):
                        combined_data[key].extend(value)
                    else:
                        combined_data[key].append(value)
                else:
                    # 实现：非列表值转为列表后追加
                    combined_data[key] = (
                        [combined_data[key], value] if not isinstance(value, list) else [combined_data[key], *value]
                    )

        if evaluate:
            combined_data = self.recursive_eval(combined_data)

        return Data(**combined_data)

    def filter_data(self, input_data: list[dict[str, Any]], filter_key: str, filter_value: str, operator: str) -> list:
        """按指定条件过滤列表项。"""
        if not input_data:
            self.status = "Input data is empty."
            return []

        if not filter_key or not filter_value:
            self.status = "Filter key or value is missing."
            return input_data

        filtered_data = []
        for item in input_data:
            if isinstance(item, dict) and filter_key in item:
                if self.compare_values(item[filter_key], filter_value, operator):
                    filtered_data.append(item)
            else:
                self.status = f"Warning: Some items don't have the key '{filter_key}' or are not dictionaries."

        return filtered_data

    def compare_values(self, item_value: Any, filter_value: str, operator: str) -> bool:
        """按操作符比较值。"""
        comparison_func = OPERATORS.get(operator)
        if comparison_func:
            return comparison_func(item_value, filter_value)
        return False

    def multi_filter_data(self) -> Data:
        """对列表字段应用多条件过滤。"""
        self.validate_single_data("Filter Values")
        data_filtered = self.get_normalized_data()

        for filter_key in self.filter_key:
            if filter_key not in data_filtered:
                msg = f"Filter key '{filter_key}' not found in data. Available keys: {list(data_filtered.keys())}"
                raise ValueError(msg)

            if isinstance(data_filtered[filter_key], list):
                for filter_data in self.filter_values:
                    filter_value = self.filter_values.get(filter_data)
                    if filter_value is not None:
                        data_filtered[filter_key] = self.filter_data(
                            input_data=data_filtered[filter_key],
                            filter_key=filter_data,
                            filter_value=filter_value,
                            operator=self.operator,
                        )
            else:
                msg = f"Filter key '{filter_key}' is not a list."
                raise TypeError(msg)

        return Data(**data_filtered)

    def append_update(self) -> Data:
        """追加或更新键值对。"""
        self.validate_single_data("Append or Update")
        data_filtered = self.get_normalized_data()

        for key, value in self.append_update_data.items():
            data_filtered[key] = value

        return Data(**data_filtered)

    # 配置与执行方法
    def update_build_config(self, build_config: dotdict, field_value: Any, field_name: str | None = None) -> dotdict:
        """根据所选操作动态调整输入配置。

        关键路径（三步）：
        1) 处理操作选择并更新列表输入模式；
        2) 按操作显示/隐藏相关字段；
        3) 解析路径选择示例并更新下拉项。
        """
        if field_name == "operations":
            build_config["operations"]["value"] = field_value
            selected_actions = [action["name"] for action in field_value]
            if len(selected_actions) == 1 and selected_actions[0] in ACTION_CONFIG:
                action = selected_actions[0]
                config = ACTION_CONFIG[action]
                build_config["data"]["is_list"] = config["is_list"]
                logger.info(config["log_msg"])
                return set_current_fields(
                    build_config=build_config,
                    action_fields=self.actions_data,
                    selected_action=action,
                    default_fields=["operations", "data"],
                    func=set_field_display,
                )

        if field_name == "mapped_json_display":
            try:
                parsed_json = json.loads(field_value)
                keys = DataOperationsComponent.extract_all_paths(parsed_json)
                build_config["selected_key"]["options"] = keys
                build_config["selected_key"]["show"] = True
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                logger.error(f"Error parsing mapped JSON: {e}")
                build_config["selected_key"]["show"] = False

        return build_config

    def json_path(self) -> Data:
        """根据选定路径提取 JSON 值。"""
        try:
            if not self.data or not self.selected_key:
                msg = "Missing input data or selected key."
                raise ValueError(msg)
            input_payload = self.data[0].data if isinstance(self.data, list) else self.data.data
            compiled = jq.compile(self.selected_key)
            result = compiled.input(input_payload).first()
            if isinstance(result, dict):
                return Data(data=result)
            return Data(data={"result": result})
        except (ValueError, TypeError, KeyError) as e:
            self.status = f"Error: {e!s}"
            self.log(self.status)
            return Data(data={"error": str(e)})

    def as_data(self) -> Data:
        """根据当前选择的操作执行并返回 Data。

        关键路径（三步）：
        1) 校验操作选择；
        2) 映射到处理函数并执行；
        3) 返回结果或空 Data。
        """
        if not hasattr(self, "operations") or not self.operations:
            return Data(data={})

        selected_actions = [action["name"] for action in self.operations]
        logger.info(f"selected_actions: {selected_actions}")
        if len(selected_actions) != 1:
            return Data(data={})

        action_map: dict[str, Callable[[], Data]] = {
            "Select Keys": self.select_keys,
            "Literal Eval": self.evaluate_data,
            "Combine": self.combine_data,
            "Filter Values": self.multi_filter_data,
            "Append or Update": self.append_update,
            "Remove Keys": self.remove_keys,
            "Rename Keys": self.rename_keys,
            "Path Selection": self.json_path,
            "JQ Expression": self.json_query,
        }
        handler: Callable[[], Data] | None = action_map.get(selected_actions[0])
        if handler:
            try:
                return handler()
            except Exception as e:
                logger.error(f"Error executing {selected_actions[0]}: {e!s}")
                raise
        return Data(data={})
