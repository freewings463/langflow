"""动态表单创建 Data 组件。

本模块根据表格配置动态生成输入字段，并将结果汇总为 Data 或 Message。
主要功能包括：
- 动态输入字段生成（支持文本/数字/布尔/Handle）
- 提取连接或手动输入的值并生成结构化输出

注意事项：字段配置来自 `form_fields`，为空时输出空数据。
"""

from typing import Any

from lfx.custom import Component
from lfx.io import (
    BoolInput,
    FloatInput,
    HandleInput,
    IntInput,
    MultilineInput,
    Output,
    StrInput,
    TableInput,
)
from lfx.schema.data import Data
from lfx.schema.message import Message


class DynamicCreateDataComponent(Component):
    """动态表单 Data 组件封装。

    契约：输入为表单配置与动态字段；输出为 `Data` 或 `Message`。
    副作用：更新 `self.status` 并记录连接信息。
    失败语义：通常不抛异常，异常值会被转为字符串。
    """
    display_name: str = "Dynamic Create Data"
    description: str = "Dynamically create a Data with a specified number of fields."
    name: str = "DynamicCreateData"
    MAX_FIELDS = 15  # 最大字段数（表单配置建议限制）
    icon = "ListFilter"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    inputs = [
        TableInput(
            name="form_fields",
            display_name="Input Configuration",
            info=(
                "Define the dynamic form fields. Each row creates a new input field "
                "that can connect to other components."
            ),
            table_schema=[
                {
                    "name": "field_name",
                    "display_name": "Field Name",
                    "type": "str",
                    "description": "Name for the field (used as both internal name and display label)",
                },
                {
                    "name": "field_type",
                    "display_name": "Field Type",
                    "type": "str",
                    "description": "Type of input field to create",
                    "options": ["Text", "Data", "Number", "Handle", "Boolean"],
                    "value": "Text",
                },
            ],
            value=[],
            real_time_refresh=True,
        ),
        BoolInput(
            name="include_metadata",
            display_name="Include Metadata",
            info="Include form configuration metadata in the output.",
            value=False,
            advanced=True,
        ),
    ]

    outputs = [
        Output(display_name="Data", name="form_data", method="process_form"),
        Output(display_name="Message", name="message", method="get_message"),
    ]

    def update_build_config(self, build_config: dict, field_value: Any, field_name: str | None = None) -> dict:
        """根据表单配置生成动态输入字段。

        关键路径（三步）：
        1) 清理旧的动态字段；
        2) 遍历表单配置并映射输入类型；
        3) 回写动态字段到构建配置。
        """
        if field_name == "form_fields":
            # 实现：清理旧的动态字段
            keys_to_remove = [key for key in build_config if key.startswith("dynamic_")]
            for key in keys_to_remove:
                del build_config[key]

            # 实现：根据表格配置生成动态字段
            if field_value is None:
                field_value = []

            for i, field_config in enumerate(field_value):
                if field_config is None:
                    continue

                field_name = field_config.get("field_name", f"field_{i}")
                display_name = field_name
                field_type_option = field_config.get("field_type", "Text")
                default_value = ""
                required = False
                help_text = ""

                # 实现：映射字段类型到具体输入类型
                field_type_mapping = {
                    "Text": {"field_type": "multiline", "input_types": ["Text", "Message"]},
                    "Data": {"field_type": "data", "input_types": ["Data"]},
                    "Number": {"field_type": "number", "input_types": ["Text", "Message"]},
                    "Handle": {"field_type": "handle", "input_types": ["Text", "Data", "Message"]},
                    "Boolean": {"field_type": "boolean", "input_types": None},
                }

                field_config_mapped = field_type_mapping.get(
                    field_type_option, {"field_type": "text", "input_types": []}
                )
                if not isinstance(field_config_mapped, dict):
                    field_config_mapped = {"field_type": "text", "input_types": []}
                field_type = field_config_mapped["field_type"]
                input_types_list = field_config_mapped["input_types"]

                dynamic_input_name = f"dynamic_{field_name}"

                if field_type == "text":
                    if input_types_list:
                        build_config[dynamic_input_name] = StrInput(
                            name=dynamic_input_name,
                            display_name=display_name,
                            info=f"{help_text} (Can connect to: {', '.join(input_types_list)})",
                            value=default_value,
                            required=required,
                            input_types=input_types_list,
                        )
                    else:
                        build_config[dynamic_input_name] = StrInput(
                            name=dynamic_input_name,
                            display_name=display_name,
                            info=help_text,
                            value=default_value,
                            required=required,
                        )

                elif field_type == "multiline":
                    if input_types_list:
                        build_config[dynamic_input_name] = MultilineInput(
                            name=dynamic_input_name,
                            display_name=display_name,
                            info=f"{help_text} (Can connect to: {', '.join(input_types_list)})",
                            value=default_value,
                            required=required,
                            input_types=input_types_list,
                        )
                    else:
                        build_config[dynamic_input_name] = MultilineInput(
                            name=dynamic_input_name,
                            display_name=display_name,
                            info=help_text,
                            value=default_value,
                            required=required,
                        )

                elif field_type == "number":
                    try:
                        default_int = int(default_value) if default_value else 0
                    except ValueError:
                        default_int = 0

                    if input_types_list:
                        build_config[dynamic_input_name] = IntInput(
                            name=dynamic_input_name,
                            display_name=display_name,
                            info=f"{help_text} (Can connect to: {', '.join(input_types_list)})",
                            value=default_int,
                            required=required,
                            input_types=input_types_list,
                        )
                    else:
                        build_config[dynamic_input_name] = IntInput(
                            name=dynamic_input_name,
                            display_name=display_name,
                            info=help_text,
                            value=default_int,
                            required=required,
                        )

                elif field_type == "float":
                    try:
                        default_float = float(default_value) if default_value else 0.0
                    except ValueError:
                        default_float = 0.0

                    if input_types_list:
                        build_config[dynamic_input_name] = FloatInput(
                            name=dynamic_input_name,
                            display_name=display_name,
                            info=f"{help_text} (Can connect to: {', '.join(input_types_list)})",
                            value=default_float,
                            required=required,
                            input_types=input_types_list,
                        )
                    else:
                        build_config[dynamic_input_name] = FloatInput(
                            name=dynamic_input_name,
                            display_name=display_name,
                            info=help_text,
                            value=default_float,
                            required=required,
                        )

                elif field_type == "boolean":
                    default_bool = default_value.lower() in ["true", "1", "yes"] if default_value else False

                    # 注意：布尔字段不使用 input_types
                    build_config[dynamic_input_name] = BoolInput(
                        name=dynamic_input_name,
                        display_name=display_name,
                        info=help_text,
                        value=default_bool,
                        input_types=[],
                        required=required,
                    )

                elif field_type == "handle":
                    # 实现：通用 Handle 连接
                    build_config[dynamic_input_name] = HandleInput(
                        name=dynamic_input_name,
                        display_name=display_name,
                        info=f"{help_text} (Accepts: {', '.join(input_types_list) if input_types_list else 'Any'})",
                        input_types=input_types_list if input_types_list else ["Data", "Text", "Message"],
                        required=required,
                    )

                elif field_type == "data":
                    # 实现：仅接受 Data 类型
                    build_config[dynamic_input_name] = HandleInput(
                        name=dynamic_input_name,
                        display_name=display_name,
                        info=f"{help_text} (Data input)",
                        input_types=input_types_list if input_types_list else ["Data"],
                        required=required,
                    )

                else:
                    # 注意：未知类型回退为文本输入
                    build_config[dynamic_input_name] = StrInput(
                        name=dynamic_input_name,
                        display_name=display_name,
                        info=f"{help_text} (Unknown type '{field_type}', defaulting to text)",
                        value=default_value,
                        required=required,
                    )

        return build_config

    def get_dynamic_values(self) -> dict[str, Any]:
        """提取动态字段的简化值。

        关键路径（三步）：
        1) 遍历表单字段并读取动态值；
        2) 提取简化值并记录连接类型；
        3) 返回字段值字典。
        """
        dynamic_values = {}
        connection_info = {}
        form_fields = getattr(self, "form_fields", [])

        for field_config in form_fields:
            if field_config is None:
                continue

            field_name = field_config.get("field_name", "")
            if field_name:
                dynamic_input_name = f"dynamic_{field_name}"
                value = getattr(self, dynamic_input_name, None)

                if value is not None:
                    try:
                        extracted_value = self._extract_simple_value(value)
                        dynamic_values[field_name] = extracted_value

                        # 实现：记录连接类型便于状态输出
                        if hasattr(value, "text") and hasattr(value, "timestamp"):
                            connection_info[field_name] = "Connected (Message)"
                        elif hasattr(value, "data"):
                            connection_info[field_name] = "Connected (Data)"
                        elif isinstance(value, (str, int, float, bool, list, dict)):
                            connection_info[field_name] = "Manual input"
                        else:
                            connection_info[field_name] = "Connected (Object)"

                    except (AttributeError, TypeError, ValueError):
                        # 注意：兜底为字符串
                        dynamic_values[field_name] = str(value)
                        connection_info[field_name] = "Error"
                else:
                    dynamic_values[field_name] = ""
                    connection_info[field_name] = "Empty default"

        self._connection_info = connection_info
        return dynamic_values

    def _extract_simple_value(self, value: Any) -> Any:
        """从任意类型中提取可用的简单值。"""
        if value is None:
            return None

        if isinstance(value, (str, int, float, bool)):
            return value

        if isinstance(value, (list, tuple)):
            return [self._extract_simple_value(item) for item in value]

        if isinstance(value, dict):
            return {str(k): self._extract_simple_value(v) for k, v in value.items()}

        if hasattr(value, "text"):
            return str(value.text) if value.text is not None else ""

        if hasattr(value, "data") and value.data is not None:
            return self._extract_simple_value(value.data)

        return str(value)

    def process_form(self) -> Data:
        """处理表单并输出 Data。

        关键路径（三步）：
        1) 汇总动态字段的简化值；
        2) 统计连接情况并写入状态；
        3) 返回包含字段值的 Data。
        """
        dynamic_values = self.get_dynamic_values()

        connected_fields = len([v for v in getattr(self, "_connection_info", {}).values() if "Connected" in v])
        total_fields = len(dynamic_values)

        self.status = f"Form processed successfully. {connected_fields}/{total_fields} fields connected to components."

        return Data(data=dynamic_values)

    def get_message(self) -> Message:
        """将表单数据格式化为文本消息。

        关键路径（三步）：
        1) 汇总动态字段值；
        2) 构建可读的多行文本；
        3) 写入状态并返回 Message。
        """
        dynamic_values = self.get_dynamic_values()

        if not dynamic_values:
            return Message(text="No form data available")

        message_lines = ["📋 Form Data:"]
        message_lines.append("=" * 40)

        for field_name, value in dynamic_values.items():
            display_name = field_name

            message_lines.append(f"• {display_name}: {value}")

        message_lines.append("=" * 40)
        message_lines.append(f"Total fields: {len(dynamic_values)}")

        message_text = "\n".join(message_lines)
        self.status = f"Message formatted with {len(dynamic_values)} fields"

        return Message(text=message_text)
