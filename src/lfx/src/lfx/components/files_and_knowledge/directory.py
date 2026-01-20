"""
模块名称：目录加载组件

本模块提供目录级文件加载能力，按类型/深度批量读取并输出为 Data 或 DataFrame。
主要功能包括：
- 递归/非递归扫描目录并过滤文件类型
- 支持多线程并发加载文本类文件
- 输出统一的 `Data` 列表或 `DataFrame`

关键组件：
- DirectoryComponent：目录读取组件

设计背景：统一文件批量加载入口，减少上层对文件系统细节的处理。
注意事项：`types` 必须为受支持的文本类型；非法类型会抛 `ValueError`。
"""

from lfx.base.data.utils import TEXT_FILE_TYPES, parallel_load_data, parse_text_file_to_data, retrieve_file_paths
from lfx.custom.custom_component.component import Component
from lfx.io import BoolInput, IntInput, MessageTextInput, MultiselectInput
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame
from lfx.template.field.base import Output


class DirectoryComponent(Component):
    """目录加载组件。

    契约：`path` 必须为可解析的目录路径；`types` 需属于 `TEXT_FILE_TYPES`。
    副作用：读取文件系统并产生日志/状态更新。
    失败语义：非法类型或路径问题会抛 `ValueError`/`OSError`。
    """

    display_name = "Directory"
    description = "Recursively load files from a directory."
    documentation: str = "https://docs.langflow.org/directory"
    icon = "folder"
    name = "Directory"

    inputs = [
        MessageTextInput(
            name="path",
            display_name="Path",
            info="Path to the directory to load files from. Defaults to current directory ('.')",
            value=".",
            tool_mode=True,
        ),
        MultiselectInput(
            name="types",
            display_name="File Types",
            info="File types to load. Select one or more types or leave empty to load all supported types.",
            options=TEXT_FILE_TYPES,
            value=[],
        ),
        IntInput(
            name="depth",
            display_name="Depth",
            info="Depth to search for files.",
            value=0,
        ),
        IntInput(
            name="max_concurrency",
            display_name="Max Concurrency",
            advanced=True,
            info="Maximum concurrency for loading files.",
            value=2,
        ),
        BoolInput(
            name="load_hidden",
            display_name="Load Hidden",
            advanced=True,
            info="If true, hidden files will be loaded.",
        ),
        BoolInput(
            name="recursive",
            display_name="Recursive",
            advanced=True,
            info="If true, the search will be recursive.",
        ),
        BoolInput(
            name="silent_errors",
            display_name="Silent Errors",
            advanced=True,
            info="If true, errors will not raise an exception.",
        ),
        BoolInput(
            name="use_multithreading",
            display_name="Use Multithreading",
            advanced=True,
            info="If true, multithreading will be used.",
        ),
    ]

    outputs = [
        Output(display_name="Loaded Files", name="dataframe", method="as_dataframe"),
    ]

    def load_directory(self) -> list[Data]:
        """加载目录内文件并返回 `Data` 列表。

        关键路径（三步）：
        1) 解析路径与文件类型过滤。
        2) 构建文件列表并并发/串行读取。
        3) 过滤无效结果并写入 `status`。

        异常流：遇到非法类型或读取失败会抛 `ValueError`/`OSError`。
        """
        path = self.path
        types = self.types
        depth = self.depth
        max_concurrency = self.max_concurrency
        load_hidden = self.load_hidden
        recursive = self.recursive
        silent_errors = self.silent_errors
        use_multithreading = self.use_multithreading

        resolved_path = self.resolve_path(path)

        if not types:
            types = TEXT_FILE_TYPES

        invalid_types = [t for t in types if t not in TEXT_FILE_TYPES]
        if invalid_types:
            msg = f"Invalid file types specified: {invalid_types}. Valid types are: {TEXT_FILE_TYPES}"
            raise ValueError(msg)

        valid_types = types

        file_paths = retrieve_file_paths(
            resolved_path, load_hidden=load_hidden, recursive=recursive, depth=depth, types=valid_types
        )

        loaded_data = []
        if use_multithreading:
            loaded_data = parallel_load_data(file_paths, silent_errors=silent_errors, max_concurrency=max_concurrency)
        else:
            loaded_data = [parse_text_file_to_data(file_path, silent_errors=silent_errors) for file_path in file_paths]

        valid_data = [x for x in loaded_data if x is not None and isinstance(x, Data)]
        self.status = valid_data
        return valid_data

    def as_dataframe(self) -> DataFrame:
        """将目录加载结果包装为 `DataFrame`。"""
        return DataFrame(self.load_directory())
