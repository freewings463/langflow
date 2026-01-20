"""
模块名称：`CSV` 数据加载组件

本模块提供 `CSV` 文件/路径/字符串到 `Data` 列表的转换，主要用于将表格数据
映射为 `LangFlow` 可消费的结构。
主要功能包括：
- 从上传文件或本地路径读取 `CSV`
- 解析 `CSV` 字符串并转换为 `Data`
- 支持指定文本列键

关键组件：
- `CSVToDataComponent`

设计背景：统一数据导入入口，便于数据源替换与测试。
注意事项：必须且只能提供一种输入来源，否则抛 `ValueError`。
"""

import csv
import io
from pathlib import Path

from lfx.base.data.storage_utils import read_file_text
from lfx.custom.custom_component.component import Component
from lfx.io import FileInput, MessageTextInput, MultilineInput, Output
from lfx.schema.data import Data
from lfx.utils.async_helpers import run_until_complete


class CSVToDataComponent(Component):
    """`CSV` 转 `Data` 组件

    契约：
    - 输入：`CSV` 文件/路径/字符串（三选一）与 `text_key`
    - 输出：`Data` 列表
    - 副作用：设置 `self.status`
    - 失败语义：输入冲突或解析失败时抛 `ValueError`
    """
    display_name = "Load CSV"
    description = "Load a CSV file, CSV from a file path, or a valid CSV string and convert it to a list of Data"
    icon = "file-spreadsheet"
    name = "CSVtoData"
    legacy = True
    replacement = ["data.File"]

    inputs = [
        FileInput(
            name="csv_file",
            display_name="CSV File",
            file_types=["csv"],
            info="Upload a CSV file to convert to a list of Data objects",
        ),
        MessageTextInput(
            name="csv_path",
            display_name="CSV File Path",
            info="Provide the path to the CSV file as pure text",
        ),
        MultilineInput(
            name="csv_string",
            display_name="CSV String",
            info="Paste a CSV string directly to convert to a list of Data objects",
        ),
        MessageTextInput(
            name="text_key",
            display_name="Text Key",
            info="The key to use for the text column. Defaults to 'text'.",
            value="text",
        ),
    ]

    outputs = [
        Output(name="data_list", display_name="Data List", method="load_csv_to_data"),
    ]

    def load_csv_to_data(self) -> list[Data]:
        """加载 `CSV` 并转换为 `Data` 列表

        关键路径（三步）：
        1) 校验仅提供一种输入来源
        2) 读取 `CSV` 内容并解析
        3) 生成 `Data` 列表并更新状态

        异常流：`CSV` 解析失败抛 `ValueError`。
        性能瓶颈：大文件读取与解析。
        排障入口：异常信息与 `self.status`。
        
        契约：
        - 输入：无（使用组件字段）
        - 输出：`Data` 列表
        - 副作用：更新 `self.status`
        - 失败语义：解析失败或输入不合法时抛 `ValueError`
        """
        if sum(bool(field) for field in [self.csv_file, self.csv_path, self.csv_string]) != 1:
            msg = "Please provide exactly one of: CSV file, file path, or CSV string."
            raise ValueError(msg)

        csv_data = None
        try:
            if self.csv_file:
                # 注意：`FileInput` 返回本地文件路径
                file_path = self.csv_file
                if not file_path.lower().endswith(".csv"):
                    self.status = "The provided file must be a CSV file."
                else:
                    # 注意：解析为绝对路径并从本地读取
                    resolved_path = self.resolve_path(file_path)
                    csv_bytes = Path(resolved_path).read_bytes()
                    csv_data = csv_bytes.decode("utf-8")

            elif self.csv_path:
                file_path = self.csv_path
                if not file_path.lower().endswith(".csv"):
                    self.status = "The provided path must be to a CSV file."
                else:
                    csv_data = run_until_complete(
                        read_file_text(file_path, encoding="utf-8", resolve_path=self.resolve_path, newline="")
                    )

            else:
                csv_data = self.csv_string

            if csv_data:
                csv_reader = csv.DictReader(io.StringIO(csv_data))
                result = [Data(data=row, text_key=self.text_key) for row in csv_reader]

                if not result:
                    self.status = "The CSV data is empty."
                    return []

                self.status = result
                return result

        except csv.Error as e:
            error_message = f"CSV parsing error: {e}"
            self.status = error_message
            raise ValueError(error_message) from e

        except Exception as e:
            error_message = f"An error occurred: {e}"
            self.status = error_message
            raise ValueError(error_message) from e

        # 注意：兜底异常
        raise ValueError(self.status)
