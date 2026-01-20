"""
模块名称：base_file

本模块提供文件类组件的通用基类与处理流程，统一路径解析、bundle 解包与结果汇总。
主要功能包括：
- 功能1：处理本地与对象存储（如 `s3`）文件路径的解析与校验。
- 功能2：递归解包 `zip/tar/tgz` 等文件包并过滤不可用文件。
- 功能3：将文件处理结果汇总为 `Data`/`DataFrame`/`Message` 输出。

使用场景：文件输入类组件需要复用同一解析/解包流程时。
关键组件：
- 类 `BaseFileComponent`：文件处理流程的标准化基类。
- 类 `BaseFile`：文件与 `Data` 元数据的轻量封装。

设计背景：多种文件组件共享同一处理链路，降低重复实现与行为偏差。
注意事项：对象存储路径不做本地 `exists()` 校验；bundle 解包需防路径穿越。
"""

import ast
import shutil
import tarfile
from abc import ABC, abstractmethod
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any
from zipfile import ZipFile, is_zipfile

import orjson
import pandas as pd

from lfx.base.data.storage_utils import get_file_size, read_file_bytes
from lfx.custom.custom_component.component import Component
from lfx.io import BoolInput, FileInput, HandleInput, Output, StrInput
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame
from lfx.schema.message import Message
from lfx.services.deps import get_settings_service
from lfx.utils.async_helpers import run_until_complete
from lfx.utils.helpers import build_content_type_from_extension

if TYPE_CHECKING:
    from collections.abc import Callable


class BaseFileComponent(Component, ABC):
    """文件类组件基类，封装解析、校验、解包与结果汇总流程。

    契约：子类必须声明 `VALID_EXTENSIONS` 并实现 `process_files`；输入来自 `path`/`file_path`，输出为 `Data`/`Message`/`DataFrame`。
    关键路径：
    1) `_validate_and_resolve_paths` 构建 `BaseFile`；
    2) `_unpack_and_collect_files` 递归解包与收集；
    3) `process_files` 产出 `Data` 并由 `load_files_*` 汇总。
    决策：
    问题：文件组件重复实现路径与解包逻辑导致行为不一致。
    方案：提供统一基类与扩展点（仅由子类实现解析）。
    代价：子类需遵守固定输入/输出约束。
    重评：当新增存储类型或流程阶段超出当前扩展点时。
    """

    class BaseFile:
        """文件与 `Data` 元数据的轻量封装。

        契约：`data` 必须是 `Data` 或 `Data` 列表；`path` 为待处理文件路径。
        关键路径：封装 `data/path` 并在合并阶段使用 `merge_data`。
        决策：
        问题：处理链路需要在单个对象中携带路径与多份 `Data`。
        方案：封装为 `BaseFile` 并在合并阶段使用 `merge_data`。
        代价：额外的封装对象与拷贝成本。
        重评：当 `Data` 结构改为流式或分页返回时。
        """

        def __init__(
            self,
            data: Data | list[Data],
            path: Path,
            *,
            delete_after_processing: bool = False,
            silent_errors: bool = False,
        ):
            """初始化文件封装对象。

            契约：`data` 仅接受 `Data` 或其列表；`delete_after_processing` 为 True 时由外层清理。
            关键路径：标准化 `data` 列表并记录删除与静默错误策略。
            决策：
            问题：处理阶段需携带删除策略与静默错误开关。
            方案：在对象内保存 `delete_after_processing` 与 `_silent_errors`。
            代价：生命周期由调用方负责，需确保清理时机一致。
            重评：当删除策略迁移到统一资源管理器时。
            """
            self._data = data if isinstance(data, list) else [data]
            self.path = path
            self.delete_after_processing = delete_after_processing
            self._silent_errors = silent_errors

        @property
        def data(self) -> list[Data]:
            """获取当前文件关联的 `Data` 列表。

            契约：始终返回列表；为空时返回空列表。
            关键路径：直接暴露内部 `_data` 列表。
            决策：
            问题：调用方需要统一遍历语义。
            方案：使用列表作为唯一返回形态。
            代价：单条数据需进行列表包装。
            重评：当 `Data` 统一为惰性迭代器时。
            """
            return self._data or []

        @data.setter
        def data(self, value: Data | list[Data]):
            """设置 `Data` 列表并强制类型约束。

            契约：仅接受 `Data` 或其列表；非法类型在 `silent_errors=False` 时抛 `ValueError`。
            关键路径：类型收敛 -> 验证 -> 赋值。
            决策：
            问题：避免混入非 `Data` 对象导致下游序列化失败。
            方案：在 setter 中做类型收敛与验证。
            代价：运行期增加一次类型检查。
            重评：当引入静态类型检查并覆盖运行期边界时。
            """
            if isinstance(value, Data):
                self._data = [value]
            elif isinstance(value, list) and all(isinstance(item, Data) for item in value):
                self._data = value
            else:
                msg = f"data must be a Data object or a list of Data objects. Got: {type(value)}"
                if not self._silent_errors:
                    raise ValueError(msg)

        def merge_data(self, new_data: Data | list[Data] | None) -> list[Data]:
            """合并新 `Data` 到当前 `data`，生成组合后的列表。

            契约：`new_data` 为 `Data`/列表/None；返回列表长度为笛卡尔积或原始列表。
            关键路径：标准化 `new_data` -> 逐项合并字典。
            决策：
            问题：需要在保留原始 `Data` 的同时叠加新字段。
            方案：对每个原始 `Data` 与新 `Data` 做字典合并。
            代价：在数据量大时产生额外对象与内存占用。
            重评：当 `Data` 支持增量合并或视图模式时。
            """
            if new_data is None:
                return self.data

            if isinstance(new_data, Data):
                new_data_list = [new_data]
            elif isinstance(new_data, list) and all(isinstance(item, Data) for item in new_data):
                new_data_list = new_data
            else:
                msg = "new_data must be a Data object, a list of Data objects, or None."
                if not self._silent_errors:
                    raise ValueError(msg)
                return self.data

            return [
                Data(data={**data.data, **new_data_item.data}) for data in self.data for new_data_item in new_data_list
            ]

        def __str__(self):
            """生成用于日志与调试的摘要字符串。

            契约：不抛异常；文本预览最多 50 字符。
            关键路径：根据 `data` 数量选择不同预览策略。
            决策：
            问题：需要在日志中快速定位文件及数据规模。
            方案：短预览 + 数量摘要。
            代价：可能截断导致信息不完整。
            重评：当日志系统支持结构化字段展示时。
            """
            if len(self.data) == 0:
                text_preview = ""
            elif len(self.data) == 1:
                max_text_length = 50
                text_preview = self.data.get_text()[:max_text_length]
                if len(self.data.get_text()) > max_text_length:
                    text_preview += "..."
                text_preview = f"text_preview='{text_preview}'"
            else:
                text_preview = f"{len(self.data)} data objects"
            return f"BaseFile(path={self.path}, delete_after_processing={self.delete_after_processing}, {text_preview}"

    # 注意：子类通过覆写类变量定义支持的扩展名集合。
    VALID_EXTENSIONS: list[str] = []  # 注意：由子类覆写
    IGNORE_STARTS_WITH = [".", "__MACOSX"]

    SERVER_FILE_PATH_FIELDNAME = "file_path"
    SUPPORTED_BUNDLE_EXTENSIONS = ["zip", "tar", "tgz", "bz2", "gz"]

    def __init__(self, *args, **kwargs):
        """初始化基类并同步输入提示。

        契约：基于 `valid_extensions` 更新 `FileInput.file_types/info`。
        关键路径：读取扩展名 -> 更新输入类型 -> 更新提示文案。
        决策：
        问题：不同子类的扩展名需反映到 UI 提示。
        方案：在实例化时动态填充 `FileInput` 的类型与说明。
        代价：运行期修改输入描述，需保持与 `VALID_EXTENSIONS` 一致。
        重评：当 UI 提示改为静态配置或注册表驱动时。
        """
        super().__init__(*args, **kwargs)
        # 实现：根据子类扩展名动态更新 `FileInput` 的文件类型提示。
        self.get_base_inputs()[0].file_types = [
            *self.valid_extensions,
            *self.SUPPORTED_BUNDLE_EXTENSIONS,
        ]

        file_types = ", ".join(self.valid_extensions)
        bundles = ", ".join(self.SUPPORTED_BUNDLE_EXTENSIONS)
        self.get_base_inputs()[
            0
        ].info = f"Supported file extensions: {file_types}; optionally bundled in file extensions: {bundles}"

    _base_inputs = [
        FileInput(
            name="path",
            display_name="Files",
            fileTypes=[],  # 注意：在 `__init__` 动态设置
            info="",  # 注意：在 `__init__` 动态设置
            required=False,
            list=True,
            value=[],
            tool_mode=True,
        ),
        HandleInput(
            name="file_path",
            display_name="Server File Path",
            info=(
                f"Data object with a '{SERVER_FILE_PATH_FIELDNAME}' property pointing to server file"
                " or a Message object with a path to the file. Supercedes 'Path' but supports same file types."
            ),
            required=False,
            input_types=["Data", "Message"],
            is_list=True,
            advanced=True,
        ),
        StrInput(
            name="separator",
            display_name="Separator",
            value="\n\n",
            show=True,
            info="Specify the separator to use between multiple outputs in Message format.",
            advanced=True,
        ),
        BoolInput(
            name="silent_errors",
            display_name="Silent Errors",
            advanced=True,
            info="If true, errors will not raise an exception.",
        ),
        BoolInput(
            name="delete_server_file_after_processing",
            display_name="Delete Server File After Processing",
            advanced=True,
            value=True,
            info="If true, the Server File Path will be deleted after processing.",
        ),
        BoolInput(
            name="ignore_unsupported_extensions",
            display_name="Ignore Unsupported Extensions",
            advanced=True,
            value=True,
            info="If true, files with unsupported extensions will not be processed.",
        ),
        BoolInput(
            name="ignore_unspecified_files",
            display_name="Ignore Unspecified Files",
            advanced=True,
            value=False,
            info=f"If true, Data with no '{SERVER_FILE_PATH_FIELDNAME}' property will be ignored.",
        ),
    ]

    _base_outputs = [
        Output(display_name="Files", name="dataframe", method="load_files"),
    ]

    @abstractmethod
    def process_files(self, file_list: list[BaseFile]) -> list[BaseFile]:
        """处理文件列表并回填解析结果。

        契约：输入/输出均为 `BaseFile` 列表；子类需更新 `BaseFile.data`。
        关键路径：
        1) 读取 `BaseFile.path`；
        2) 解析并生成 `Data`；
        3) 返回更新后的列表。
        异常流：解析失败可抛异常；`silent_errors=True` 时建议返回空 `Data`。
        排障入口：建议在子类记录 `file_path` 与解析阶段关键字。
        决策：
        问题：不同文件格式解析差异大，难以在基类内统一。
        方案：保留扩展点，仅由子类实现具体解析。
        代价：子类必须遵循 `BaseFile` 协议与错误语义。
        重评：当解析策略可统一到插件化框架时。
        """

    def load_files_base(self) -> list[Data]:
        """加载文件并处理 bundle，返回解析后的 `Data` 列表。

        契约：读取 `path`/`file_path`，输出 `Data` 列表；副作用：创建临时目录、可能删除源文件。
        关键路径：
        1) 校验路径并生成 `BaseFile`；
        2) 递归解包与过滤文件；
        3) 调用 `process_files` 并扁平化输出。
        异常流：路径不存在/扩展不支持/解析失败会抛异常。
        性能瓶颈：大目录递归与压缩包解包。
        排障入口：日志关键字 `Resolved storage path`、`Unpacked bundle`。
        决策：
        问题：临时目录与删除策略容易在异常路径中泄漏。
        方案：统一在 `finally` 中清理与删除。
        代价：必须保证 `final_files` 初始化以免清理逻辑报错。
        重评：当引入统一资源管理器时。
        """
        self._temp_dirs: list[TemporaryDirectory] = []
        final_files = []  # 注意：提前初始化，避免异常路径触发 `UnboundLocalError`。
        try:
            files = self._validate_and_resolve_paths()

            all_files = self._unpack_and_collect_files(files)

            final_files = self._filter_and_mark_files(all_files)

            processed_files = self.process_files(final_files)

            # 实现：扁平化 `Data` 列表返回给下游。
            return [data for file in processed_files for data in file.data if file.data]

        finally:
            # 实现：集中清理临时目录与删除标记文件，避免异常路径泄漏资源。
            for temp_dir in self._temp_dirs:
                temp_dir.cleanup()
            for file in final_files:
                if file.delete_after_processing and file.path.exists():
                    if file.path.is_dir():
                        shutil.rmtree(file.path)
                    else:
                        file.path.unlink()

    def load_files_core(self) -> list[Data]:
        """加载文件并保证至少返回一条 `Data`。

        契约：输出非空列表；若无数据则返回包含空 `Data()` 的列表。
        关键路径：调用 `load_files_base` 并在空结果时补位。
        决策：
        问题：下游组件默认期望至少一条 `Data`。
        方案：为空时返回 `Data()` 占位。
        代价：可能掩盖真正的“无输入”场景。
        重评：当下游可显式处理空列表时。
        """
        data_list = self.load_files_base()
        if not data_list:
            return [Data()]
        return data_list

    def _extract_file_metadata(self, data_item) -> dict:
        """从带 `file_path` 的数据对象中抽取文件元数据。

        契约：输入需包含 `file_path` 属性；输出包含 `filename`/`file_size`/`mimetype` 等字段。
        关键路径：
        1) 根据存储类型获取大小；
        2) 由扩展名推断 `mimetype`；
        3) 合并 `data` 内同名字段。
        异常流：获取大小失败时返回 0，不抛异常。
        决策：
        问题：文件元信息可能来自路径与 `Data.data` 两处。
        方案：先推断基础元信息，再允许 `data` 覆盖。
        代价：调用方提供的字段可能覆盖自动推断结果。
        重评：当 metadata 统一由存储服务提供时。
        """
        metadata: dict[str, Any] = {}
        if not hasattr(data_item, "file_path"):
            return metadata

        file_path = data_item.file_path
        file_path_obj = Path(file_path)
        filename = file_path_obj.name

        settings = get_settings_service().settings
        if settings.storage_type == "s3":
            try:
                file_size = get_file_size(file_path)
            except (FileNotFoundError, ValueError):
                # 注意：无法获取大小时回退为 0，避免阻断主流程。
                file_size = 0
        else:
            try:
                file_size_stat = file_path_obj.stat()
                file_size = file_size_stat.st_size
            except OSError:
                file_size = 0

        # 实现：基础元信息字段。
        metadata["filename"] = filename
        metadata["file_size"] = file_size

        # 实现：基于扩展名推断 `mimetype`。
        extension = filename.split(".")[-1]
        if extension:
            metadata["mimetype"] = build_content_type_from_extension(extension)

        # 注意：允许 `data` 内字段覆盖推断结果。
        if hasattr(data_item, "data") and isinstance(data_item.data, dict):
            metadata_fields = ["mimetype", "file_size", "created_time", "modified_time"]
            for field in metadata_fields:
                if field in data_item.data:
                    metadata[field] = data_item.data[field]

        return metadata

    def _extract_text(self, data_item) -> str:
        """从 `Data` 或类 Data 对象提取文本内容。

        契约：返回字符串；优先 `get_text()`，其次 `data['text']`，最后 `str()`。
        关键路径：`get_text()` -> `data['text']` -> `str()`。
        决策：
        问题：不同 `Data` 实现的文本入口不一致。
        方案：按优先级尝试多种来源。
        代价：`str()` 可能包含非用户可读内容。
        重评：当 `Data` 接口统一暴露 `text` 属性时。
        """
        if isinstance(data_item.data, dict):
            text = getattr(data_item, "get_text", lambda: None)() or data_item.data.get("text")
            return text if text is not None else str(data_item)
        return str(data_item)

    def load_files_message(self) -> Message:
        """加载文件并拼接为单个 `Message`。

        契约：返回 `Message`；`text` 为多文件内容拼接；metadata 取首条 `Data`。
        关键路径：
        1) `load_files_core` 获取 `Data` 列表；
        2) 抽取首条元数据；
        3) 拼接文本与降级序列化。
        异常流：单条转换失败会降级为 `str()`；核心加载异常会向上抛出。
        排障入口：可检查 `Message` 的 `metadata` 字段。
        决策：
        问题：多文件 metadata 冲突难以合并。
        方案：仅使用首条 `Data` 的 metadata。
        代价：可能丢失其他文件的元信息。
        重评：当需要多文件聚合元数据时。
        """
        data_list = self.load_files_core()
        if not data_list:
            return Message()

        # 注意：元信息只取首条，避免多文件冲突导致字段覆盖。
        metadata = self._extract_file_metadata(data_list[0])

        sep: str = getattr(self, "separator", "\n\n") or "\n\n"
        parts: list[str] = []
        for d in data_list:
            try:
                data_text = self._extract_text(d)
                if data_text and isinstance(data_text, str):
                    parts.append(data_text)
                elif data_text:
                    # 注意：`get_text()` 返回非字符串时统一转为 `str()` 以避免拼接失败。
                    parts.append(str(data_text))
                elif isinstance(d.data, dict):
                    # 注意：无文本字段时序列化 `data`，便于排障定位结构内容。
                    parts.append(orjson.dumps(d.data, option=orjson.OPT_INDENT_2, default=str).decode())
                else:
                    parts.append(str(d))
            except Exception:  # noqa: BLE001
                # 排障：单条异常不阻断整体输出，降级为 `str()` 保留最小可见性。
                parts.append(str(d))

        return Message(text=sep.join(parts), **metadata)

    def load_files_path(self) -> Message:
        """返回包含文件路径的 `Message`。

        契约：`Message.text` 为路径列表换行拼接；仅返回可用路径。
        关键路径：解析路径 -> 按存储类型筛选 -> 拼接输出。
        决策：
        问题：对象存储路径在本地不可 `exists()` 校验。
        方案：`s3` 模式跳过本地存在性检查。
        代价：路径可能在输出时已失效。
        重评：当存储服务提供批量存在性校验接口时。
        """
        files = self._validate_and_resolve_paths()
        settings = get_settings_service().settings

        # 决策：`s3` 路径是虚拟 key，不走本地 `exists()`；存在性校验延迟到读取阶段。
        if settings.storage_type == "s3":
            paths = [file.path.as_posix() for file in files]
        else:
            paths = [file.path.as_posix() for file in files if file.path.exists()]

        return Message(text="\n".join(paths) if paths else "")

    def load_files_structured_helper(self, file_path: str) -> list[dict] | None:
        """读取结构化文件并返回行级字典列表。

        契约：仅支持 `.csv/.xlsx/.parquet`；返回 `list[dict]` 或 `None`。
        关键路径：
        1) 判断存储类型与扩展名；
        2) 使用 pandas 读取（S3 使用 `BytesIO`）；
        3) `to_dict("records")` 输出。
        异常流：读取失败会由 pandas 抛异常。
        决策：
        问题：对象存储文件无法直接走路径 API。
        方案：先下载字节再用 `BytesIO` 读取。
        代价：大文件会占用内存并增加延迟。
        重评：当存储服务支持流式读取时。
        """
        if not file_path:
            return None

        # 注意：只依据扩展名选择读取器，无法识别伪装类型。
        ext = Path(file_path).suffix.lower()

        settings = get_settings_service().settings

        # 决策：`s3` 模式先下载字节流，避免依赖本地路径。
        if settings.storage_type == "s3":
            content = run_until_complete(read_file_bytes(file_path))

            if ext == ".csv":
                result = pd.read_csv(BytesIO(content))
            elif ext == ".xlsx":
                result = pd.read_excel(BytesIO(content))
            elif ext == ".parquet":
                result = pd.read_parquet(BytesIO(content))
            else:
                return None

            return result.to_dict("records")

        # 实现：本地存储直接走 pandas 读取路径，避免重复拷贝。
        file_readers: dict[str, Callable[[str], pd.DataFrame]] = {
            ".csv": pd.read_csv,
            ".xlsx": pd.read_excel,
            ".parquet": pd.read_parquet,
            # TODO：补充 sqlite/json 等结构化读取支持。
        }

        reader = file_readers.get(ext)

        if reader:
            result = reader(file_path)  # 注意：`reader` 在此已被类型收敛为可调用。
            return result.to_dict("records")

        return None

    def load_files_structured(self) -> DataFrame:
        """加载结构化文件并返回 `DataFrame`。

        契约：仅首条 `Data` 的 `file_path` 会触发结构化读取；否则返回首条 `data` 字典。
        关键路径：
        1) `load_files_core` 获取 `Data`；
        2) 若 `file_path` 指向表格文件则走 pandas 读取；
        3) 否则直接使用 `data` 字典。
        异常流：pandas 解析异常向上抛出。
        决策：
        问题：结构化解析成本高，且多文件合并语义不明确。
        方案：仅处理首条文件并返回行级结果。
        代价：忽略后续文件内容。
        重评：当需要多文件合并与 schema 对齐时。
        """
        data_list = self.load_files_core()
        if not data_list:
            return DataFrame()

        # 注意：只取首条 `Data` 的 `file_path` 作为结构化读取入口。
        file_path = data_list[0].data.get(self.SERVER_FILE_PATH_FIELDNAME, None)

        if file_path and str(file_path).lower().endswith((".csv", ".xlsx", ".parquet")):
            rows = self.load_files_structured_helper(file_path)
        else:
            # 注意：非结构化文件默认直接输出 `data` 字段。
            rows = [data_list[0].data]

        self.status = DataFrame(rows)

        return DataFrame(rows)

    def parse_string_to_dict(self, s: str) -> dict:
        """将字符串解析为字典，兼容 JSON 与 Python 字面量。

        契约：返回字典；解析失败时返回 `{"value": s}`。
        关键路径：`orjson.loads` -> `ast.literal_eval` -> 回退包装。
        决策：
        问题：上游可能提供 JSON 或 Python 字面量字符串。
        方案：先用 `orjson`，失败后用 `ast.literal_eval`。
        代价：仅支持安全字面量，复杂对象会被降级。
        重评：当输入格式统一为 JSON 时。
        """
        # 实现：优先解析 JSON（兼容 true/false/null）。
        try:
            result = orjson.loads(s)
            if isinstance(result, dict):
                return result
        except orjson.JSONDecodeError:
            pass

        # 实现：JSON 失败后回退到 Python 字面量解析。
        try:
            result = ast.literal_eval(s)
            if isinstance(result, dict):
                return result
        except (SyntaxError, ValueError):
            pass

        # 注意：全部失败时返回原始字符串包装，避免抛异常影响流程。
        return {"value": s}

    def load_files_json(self) -> Data:
        """加载文件并返回包含 JSON 内容的单个 `Data`。

        契约：从首条 `Data` 的 `text_key` 读取字符串并解析为字典；返回 `Data(data=dict)`。
        关键路径：读取首条 `Data` -> 解析字符串 -> 写入 `Data`。
        决策：
        问题：JSON 作为文本加载后仍需结构化解析。
        方案：使用 `parse_string_to_dict` 统一解析入口。
        代价：只处理首条 `Data`，忽略多文件场景。
        重评：当 JSON 合并策略确定时。
        """
        data_list = self.load_files_core()
        if not data_list:
            return Data()

        # 注意：仅使用首条 `Data` 的 `text_key` 作为 JSON 来源。
        json_data = data_list[0].data[data_list[0].text_key]
        json_data = self.parse_string_to_dict(json_data)

        self.status = Data(data=json_data)

        return Data(data=json_data)

    def load_files(self) -> DataFrame:
        """加载文件并返回 `DataFrame`（行=文件）。

        契约：每条 `Data` 转为一行字典；包含 `file_path` 与 `text`（若存在）。
        关键路径：
        1) 读取 `Data` 列表；
        2) 展平 `Data.data` 并补充 `file_path`；
        3) 汇总为 `DataFrame`。
        决策：
        问题：`Data` 结构不固定，需提供统一表格输出。
        方案：将 `Data.data` 展平成行字典并补充 `file_path`。
        代价：嵌套结构会被原样嵌入，可能导致下游解析成本上升。
        重评：当有统一 schema 与字段映射规则时。
        """
        data_list = self.load_files_core()
        if not data_list:
            return DataFrame()

        all_rows = []
        for data in data_list:
            file_path = data.data.get(self.SERVER_FILE_PATH_FIELDNAME)
            row = dict(data.data) if data.data else {}

            # 注意：`text` 优先使用显式字段，避免 `Data.text` 的惰性计算成本。
            if "text" in data.data:
                row["text"] = data.data["text"]
            if file_path:
                row["file_path"] = file_path
            all_rows.append(row)

        self.status = DataFrame(all_rows)

        return DataFrame(all_rows)

    @property
    def valid_extensions(self) -> list[str]:
        """返回该组件允许的文件扩展名（不含点号）。

        契约：子类通过覆写 `VALID_EXTENSIONS` 提供扩展名列表。
        关键路径：直接读取类变量 `VALID_EXTENSIONS`。
        决策：
        问题：不同组件支持的文件类型不同。
        方案：将扩展名配置为类级属性，便于复用与静态检查。
        代价：运行期仍需二次校验实际文件类型。
        重评：当支持动态注册扩展名时。
        """
        return self.VALID_EXTENSIONS

    @property
    def ignore_starts_with(self) -> list[str]:
        """返回解包时需要忽略的路径前缀列表。

        契约：用于过滤隐藏文件与 `__MACOSX` 等系统目录。
        关键路径：直接返回 `IGNORE_STARTS_WITH`。
        决策：
        问题：打包文件常包含无效或系统生成内容。
        方案：通过前缀过滤减少噪声。
        代价：可能忽略用户确实需要的隐藏文件。
        重评：当提供显式白名单策略时。
        """
        return self.IGNORE_STARTS_WITH

    def rollup_data(
        self,
        base_files: list[BaseFile],
        data_list: list[Data | None],
        path_field: str = SERVER_FILE_PATH_FIELDNAME,
    ) -> list[BaseFile]:
        r"""按 `base_files` 顺序回填 `Data` 列表。

        契约：`data_list` 中必须包含 `path_field`；输出顺序与 `base_files` 保持一致。
        关键路径：
        1) 按 `path_field` 分组 `Data`；
        2) 对每个 `BaseFile` 合并对应数据；
        3) 返回新的 `BaseFile` 列表。
        异常流：缺失 `path_field` 且 `silent_errors=False` 时抛 `ValueError`。
        排障入口：日志包含缺失字段的 `Data` 内容。
        决策：
        问题：处理结果需要与原文件顺序严格对齐。
        方案：以 `base_files` 为主序，按路径回填。
        代价：若 `path_field` 不唯一会产生合并膨胀。
        重评：当引入稳定主键或索引映射时。
        """

        def _build_data_dict(data_list: list[Data | None], data_list_field: str) -> dict[str, list[Data]]:
            """按字段分组 `Data` 以便后续回填。

            契约：返回 `path_field -> Data列表` 映射。
            决策：
            问题：多文件混合输出需要快速定位归属。
            方案：预构建字典降低查找成本。
            代价：占用额外内存。
            重评：当数据量超出内存时。
            """
            data_dict: dict[str, list[Data]] = {}
            for data in data_list:
                if data is None:
                    continue
                key = data.data.get(data_list_field)
                if key is None:
                    msg = f"Data object missing required field '{data_list_field}': {data}"
                    self.log(msg)
                    if not self.silent_errors:
                        msg = f"Data object missing required field '{data_list_field}': {data}"
                        self.log(msg)
                        raise ValueError(msg)
                    continue
                data_dict.setdefault(key, []).append(data)
            return data_dict

        data_dict = _build_data_dict(data_list, path_field)

        # 注意：保持输入顺序，避免输出重排影响下游对齐。
        updated_base_files = []
        for base_file in base_files:
            new_data_list = data_dict.get(str(base_file.path), [])
            merged_data_list = base_file.merge_data(new_data_list)
            updated_base_files.append(
                BaseFileComponent.BaseFile(
                    data=merged_data_list,
                    path=base_file.path,
                    delete_after_processing=base_file.delete_after_processing,
                )
            )

        return updated_base_files

    def _file_path_as_list(self) -> list[Data]:
        """将 `file_path` 输入规整为 `Data` 列表。

        契约：支持 `Data`/`Message`/列表；非法类型在 `silent_errors=False` 时抛 `ValueError`。
        关键路径：标准化输入 -> 转换 `Message` -> 过滤非 `Data`。
        决策：
        问题：组件输入允许多种类型，需统一处理链路。
        方案：将 `Message.text` 转换为 `Data` 并统一返回列表。
        代价：输入类型越多，运行期校验越多。
        重评：当输入类型收敛为单一 `Data` 时。
        """
        file_path = self.file_path
        if not file_path:
            return []

        def _message_to_data(message: Message) -> Data:
            # 注意：`Message.text` 视为服务端文件路径。
            return Data(**{self.SERVER_FILE_PATH_FIELDNAME: message.text})

        if isinstance(file_path, Data):
            file_path = [file_path]
        elif isinstance(file_path, Message):
            file_path = [_message_to_data(file_path)]
        elif not isinstance(file_path, list):
            msg = f"Expected list of Data objects in file_path but got {type(file_path)}."
            self.log(msg)
            if not self.silent_errors:
                raise ValueError(msg)
            return []

        file_paths = []
        for obj in file_path:
            data_obj = _message_to_data(obj) if isinstance(obj, Message) else obj

            if not isinstance(data_obj, Data):
                msg = f"Expected Data object in file_path but got {type(data_obj)}."
                self.log(msg)
                if not self.silent_errors:
                    raise ValueError(msg)
                continue
            file_paths.append(data_obj)

        return file_paths

    def _validate_and_resolve_paths(self) -> list[BaseFile]:
        """校验并解析输入路径，生成 `BaseFile` 列表。

        契约：返回有效 `BaseFile`；本地路径不存在且 `silent_errors=False` 时抛 `ValueError`。
        关键路径：
        1) 统一解析 `path`/`file_path` 输入；
        2) 对本地路径进行存在性校验；
        3) 生成 `BaseFile` 并附带删除策略。
        异常流：`file_path` 缺失字段或路径不存在会抛异常（可被 `silent_errors` 抑制）。
        排障入口：日志关键字 `Resolved storage path`、`get_full_path failed`。
        决策：
        问题：对象存储路径不可用本地 `exists()` 校验。
        方案：`s3` 模式延迟校验到读取阶段。
        代价：错误会在更晚阶段暴露。
        重评：当存储服务提供轻量存在性探测时。
        """
        resolved_files = []

        def add_file(data: Data, path: str | Path, *, delete_after_processing: bool):
            path_str = str(path)
            settings = get_settings_service().settings

            # 决策：`s3` 模式不做本地存在性校验，避免误判虚拟 key。
            if settings.storage_type == "s3":
                resolved_files.append(
                    BaseFileComponent.BaseFile(data, Path(path_str), delete_after_processing=delete_after_processing)
                )
            else:
                # 注意：非绝对路径可能是存储路径格式（`flow_id/filename`），优先走 `get_full_path`。
                if "/" in path_str and not Path(path_str).is_absolute():
                    try:
                        resolved_path = Path(self.get_full_path(path_str))
                        self.log(f"Resolved storage path '{path_str}' to '{resolved_path}'")
                    except (ValueError, AttributeError) as e:
                        # 注意：`get_full_path` 失败回退到 `resolve_path`，便于兼容旧组件。
                        self.log(f"get_full_path failed for '{path_str}': {e}, falling back to resolve_path")
                        resolved_path = Path(self.resolve_path(path_str))
                else:
                    resolved_path = Path(self.resolve_path(path_str))

                if not resolved_path.exists():
                    msg = f"File not found: '{path}' (resolved to: '{resolved_path}'). Please upload the file again."
                    self.log(msg)
                    if not self.silent_errors:
                        raise ValueError(msg)
                resolved_files.append(
                    BaseFileComponent.BaseFile(data, resolved_path, delete_after_processing=delete_after_processing)
                )

        file_path = self._file_path_as_list()

        if self.path and not file_path:
            # 实现：将 `path` 规范化为 `Data`，与 `file_path` 处理流程一致。
            if isinstance(self.path, list):
                for path in self.path:
                    data_obj = Data(data={self.SERVER_FILE_PATH_FIELDNAME: path})
                    add_file(data=data_obj, path=path, delete_after_processing=False)
            else:
                data_obj = Data(data={self.SERVER_FILE_PATH_FIELDNAME: self.path})
                add_file(data=data_obj, path=self.path, delete_after_processing=False)
        elif file_path:
            for obj in file_path:
                server_file_path = obj.data.get(self.SERVER_FILE_PATH_FIELDNAME)
                if server_file_path:
                    add_file(
                        data=obj,
                        path=server_file_path,
                        delete_after_processing=self.delete_server_file_after_processing,
                    )
                elif not self.ignore_unspecified_files:
                    msg = f"Data object missing '{self.SERVER_FILE_PATH_FIELDNAME}' property."
                    self.log(msg)
                    if not self.silent_errors:
                        raise ValueError(msg)
                else:
                    msg = f"Ignoring Data object missing '{self.SERVER_FILE_PATH_FIELDNAME}' property:\n{obj}"
                    self.log(msg)

        return resolved_files

    def _unpack_and_collect_files(self, files: list[BaseFile]) -> list[BaseFile]:
        """递归解包目录/压缩包并收集为 `BaseFile`。

        契约：输入为 `BaseFile` 列表，输出仅包含文件路径（不含目录）。
        关键路径：
        1) 目录递归展开；
        2) 支持的 bundle 解包到临时目录；
        3) 直到列表中不再包含目录或 bundle。
        异常流：解包失败会抛异常；临时目录由 `load_files_base` 统一清理。
        性能瓶颈：深层目录遍历与大包解压。
        决策：
        问题：bundle 内可能仍包含目录或二级压缩包。
        方案：递归调用自身直到收敛。
        代价：递归深度增加，需注意极端输入。
        重评：当引入迭代式解包器或深度上限控制时。
        """
        collected_files = []

        for file in files:
            path = file.path
            delete_after_processing = file.delete_after_processing
            data = file.data

            if path.is_dir():
                # 实现：目录递归展开，保持与原 `BaseFile` 相同的删除策略。
                collected_files.extend(
                    [
                        BaseFileComponent.BaseFile(
                            data,
                            sub_path,
                            delete_after_processing=delete_after_processing,
                        )
                        for sub_path in path.rglob("*")
                        if sub_path.is_file()
                    ]
                )
            elif path.suffix[1:] in self.SUPPORTED_BUNDLE_EXTENSIONS:
                # 注意：解包结果写入临时目录，后续由 `load_files_base` 统一清理。
                temp_dir = TemporaryDirectory()
                self._temp_dirs.append(temp_dir)
                temp_dir_path = Path(temp_dir.name)
                self._unpack_bundle(path, temp_dir_path)
                subpaths = list(temp_dir_path.iterdir())
                self.log(f"Unpacked bundle {path.name} into {subpaths}")
                collected_files.extend(
                    [
                        BaseFileComponent.BaseFile(
                            data,
                            sub_path,
                            delete_after_processing=delete_after_processing,
                        )
                        for sub_path in subpaths
                    ]
                )
            else:
                collected_files.append(file)

        # 注意：如果仍含目录或 bundle，继续递归直到收敛。
        if any(
            file.path.is_dir() or file.path.suffix[1:] in self.SUPPORTED_BUNDLE_EXTENSIONS for file in collected_files
        ):
            return self._unpack_and_collect_files(collected_files)

        return collected_files

    def _unpack_bundle(self, bundle_path: Path, output_dir: Path):
        """解包压缩包到临时目录，并阻断路径穿越。

        契约：仅支持 `zip`/`tar` 系列；不允许解包到 `output_dir` 之外。
        关键路径：识别格式 -> 安全解包 -> 校验路径边界。
        异常流：不支持格式或检测到路径穿越时抛 `ValueError`。
        安全：对每个成员执行 `is_relative_to` 校验，避免 ZipSlip。
        决策：
        问题：解包路径可能被恶意构造导致覆盖系统文件。
        方案：逐成员校验并拒绝越界路径。
        代价：解包速度略慢。
        重评：当使用系统级安全解包库时。
        """

        def _safe_extract_zip(bundle: ZipFile, output_dir: Path):
            """安全解包 ZIP，拒绝路径穿越。"""
            for member in bundle.namelist():
                # 注意：跳过 `._` 资源分叉文件，避免噪声。
                if Path(member).name.startswith("._"):
                    continue
                member_path = output_dir / member
                # 安全：防止 `../` 路径穿越写出 `output_dir`。
                if not member_path.resolve().is_relative_to(output_dir.resolve()):
                    msg = f"Attempted Path Traversal in ZIP File: {member}"
                    raise ValueError(msg)
                bundle.extract(member, path=output_dir)

        def _safe_extract_tar(bundle: tarfile.TarFile, output_dir: Path):
            """安全解包 TAR，拒绝路径穿越。"""
            for member in bundle.getmembers():
                # 注意：跳过 `._` 资源分叉文件，避免噪声。
                if Path(member.name).name.startswith("._"):
                    continue
                member_path = output_dir / member.name
                # 安全：防止 `../` 路径穿越写出 `output_dir`。
                if not member_path.resolve().is_relative_to(output_dir.resolve()):
                    msg = f"Attempted Path Traversal in TAR File: {member.name}"
                    raise ValueError(msg)
                bundle.extract(member, path=output_dir)

        # 注意：仅允许 `zip`/`tar` 变种格式，其他格式直接拒绝。
        if is_zipfile(bundle_path):
            with ZipFile(bundle_path, "r") as zip_bundle:
                _safe_extract_zip(zip_bundle, output_dir)
        elif tarfile.is_tarfile(bundle_path):
            with tarfile.open(bundle_path, "r:*") as tar_bundle:
                _safe_extract_tar(tar_bundle, output_dir)
        else:
            msg = f"Unsupported bundle format: {bundle_path.suffix}"
            raise ValueError(msg)

    def _filter_and_mark_files(self, files: list[BaseFile]) -> list[BaseFile]:
        """按扩展名过滤文件并标记忽略列表。

        契约：返回仅包含允许扩展名的 `BaseFile`；忽略列表会写入日志。
        关键路径：本地 `is_file` 校验 -> 扩展名过滤 -> 输出结果。
        异常流：当 `ignore_unsupported_extensions=False` 且遇到不支持扩展名时抛 `ValueError`。
        决策：
        问题：本地文件可提前过滤，`s3` 路径无法验证是否为文件。
        方案：`s3` 模式跳过 `is_file()` 校验，仅做扩展名判断。
        代价：无效 `s3` 路径会在后续读取阶段失败。
        重评：当存储服务提供轻量文件类型校验时。
        """
        settings = get_settings_service().settings
        is_s3_storage = settings.storage_type == "s3"
        final_files = []
        ignored_files = []

        for file in files:
            # 注意：`s3` 为虚拟 key，无法本地判定 `is_file()`。
            if not is_s3_storage and not file.path.is_file():
                self.log(f"Not a file: {file.path.name}")
                continue

            # 注意：扩展名不在白名单时按配置决定忽略或失败。
            extension = file.path.suffix[1:].lower() if file.path.suffix else ""
            if extension not in self.valid_extensions:
                # 注意：本地存储可按配置忽略不支持扩展名。
                if not is_s3_storage and self.ignore_unsupported_extensions:
                    ignored_files.append(file.path.name)
                    continue

                msg = f"Unsupported file extension: {file.path.suffix}"
                self.log(msg)
                if not self.silent_errors:
                    raise ValueError(msg)

            final_files.append(file)

        if ignored_files:
            self.log(f"Ignored files: {ignored_files}")

        return final_files
