"""
模块名称：`JSON` 数据加载组件

本模块提供 `JSON` 文件/路径/字符串到 `Data` 或 `Data` 列表的转换，
主要用于将结构化数据导入 `LangFlow`。
主要功能包括：
- 从上传文件或本地路径读取 `JSON`
- 解析并修复轻微损坏的 `JSON` 字符串
- 输出单条 `Data` 或列表

关键组件：
- `JSONToDataComponent`

设计背景：统一数据导入入口并提高对不规范 `JSON` 的容错。
注意事项：必须且只能提供一种输入来源，否则抛 `ValueError`。
"""

import json
from pathlib import Path

from json_repair import repair_json

from lfx.base.data.storage_utils import read_file_text
from lfx.custom.custom_component.component import Component
from lfx.io import FileInput, MessageTextInput, MultilineInput, Output
from lfx.schema.data import Data
from lfx.utils.async_helpers import run_until_complete


class JSONToDataComponent(Component):
    """`JSON` 转 `Data` 组件

    契约：
    - 输入：`JSON` 文件/路径/字符串（三选一）
    - 输出：`Data` 或 `Data` 列表
    - 副作用：设置 `self.status`
    - 失败语义：输入冲突或解析失败时抛 `ValueError`
    """
    display_name = "Load JSON"
    description = (
        "Convert a JSON file, JSON from a file path, or a JSON string to a Data object or a list of Data objects"
    )
    icon = "braces"
    name = "JSONtoData"
    legacy = True
    replacement = ["data.File"]

    inputs = [
        FileInput(
            name="json_file",
            display_name="JSON File",
            file_types=["json"],
            info="Upload a JSON file to convert to a Data object or list of Data objects",
        ),
        MessageTextInput(
            name="json_path",
            display_name="JSON File Path",
            info="Provide the path to the JSON file as pure text",
        ),
        MultilineInput(
            name="json_string",
            display_name="JSON String",
            info="Enter a valid JSON string (object or array) to convert to a Data object or list of Data objects",
        ),
    ]

    outputs = [
        Output(name="data", display_name="Data", method="convert_json_to_data"),
    ]

    def convert_json_to_data(self) -> Data | list[Data]:
        """转换 `JSON` 为 `Data` 或 `Data` 列表

        关键路径（三步）：
        1) 校验仅提供一种输入来源
        2) 读取与解析 `JSON`（必要时修复）
        3) 生成 `Data` 或 `Data` 列表并更新状态

        异常流：`JSON` 解析失败抛 `ValueError`。
        性能瓶颈：大文件解析。
        排障入口：异常消息与 `self.status`。
        
        契约：
        - 输入：无（使用组件字段）
        - 输出：`Data` 或 `Data` 列表
        - 副作用：更新 `self.status`
        - 失败语义：解析失败或输入不合法时抛 `ValueError`
        """
        if sum(bool(field) for field in [self.json_file, self.json_path, self.json_string]) != 1:
            msg = "Please provide exactly one of: JSON file, file path, or JSON string."
            self.status = msg
            raise ValueError(msg)

        json_data = None

        try:
            if self.json_file:
                # 注意：`FileInput` 返回本地文件路径
                file_path = self.json_file
                if not file_path.lower().endswith(".json"):
                    self.status = "The provided file must be a JSON file."
                else:
                    # 注意：解析为绝对路径并从本地读取
                    resolved_path = self.resolve_path(file_path)
                    json_data = Path(resolved_path).read_text(encoding="utf-8")

            elif self.json_path:
                # 注意：用户路径可能是本地或对象存储键
                file_path = self.json_path
                if not file_path.lower().endswith(".json"):
                    self.status = "The provided path must be to a JSON file."
                else:
                    json_data = run_until_complete(
                        read_file_text(file_path, encoding="utf-8", resolve_path=self.resolve_path)
                    )

            else:
                json_data = self.json_string

            if json_data:
                # 注意：尝试解析 `JSON` 字符串
                try:
                    parsed_data = json.loads(json_data)
                except json.JSONDecodeError:
                    # 注意：解析失败时尝试修复 `JSON` 字符串
                    repaired_json_string = repair_json(json_data)
                    parsed_data = json.loads(repaired_json_string)

                # 注意：区分列表与对象输出
                if isinstance(parsed_data, list):
                    result = [Data(data=item) for item in parsed_data]
                else:
                    result = Data(data=parsed_data)
                self.status = result
                return result

        except (json.JSONDecodeError, SyntaxError, ValueError) as e:
            error_message = f"Invalid JSON or Python literal: {e}"
            self.status = error_message
            raise ValueError(error_message) from e

        except Exception as e:
            error_message = f"An error occurred: {e}"
            self.status = error_message
            raise ValueError(error_message) from e

        # 注意：兜底异常
        raise ValueError(self.status)
