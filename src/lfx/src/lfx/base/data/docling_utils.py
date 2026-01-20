"""
模块名称：docling_utils

本模块封装 Docling 文档解析的依赖检测、转换器缓存与 worker 处理逻辑。
主要功能包括：
- 功能1：从 `Data`/`DataFrame` 提取 `DoclingDocument`。
- 功能2：序列化/反序列化包含密钥的 Pydantic 模型配置。
- 功能3：复用 `DocumentConverter`，降低模型加载成本并支持批量处理。

使用场景：需要在多文件解析时复用 Docling 模型并提供 worker 级容错。
关键组件：
- 异常 `DoclingDependencyError`
- 函数 `extract_docling_documents`
- 函数 `_get_cached_converter`
- 函数 `docling_worker`

设计背景：Docling 模型加载耗时高，需要跨运行缓存并提供清晰的依赖错误。
注意事项：依赖为可选安装；worker 需处理 `SIGTERM/SIGINT` 的优雅退出。
"""

import importlib
import signal
import sys
import traceback
from contextlib import suppress
from functools import lru_cache

from docling_core.types.doc import DoclingDocument
from pydantic import BaseModel, SecretStr, TypeAdapter

from lfx.log.logger import logger
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame


class DoclingDependencyError(Exception):
    """用于标识 Docling 依赖缺失的异常类型。

    契约：携带 `dependency_name` 与 `install_command` 便于上游提示安装方案。
    关键路径：实例化异常 -> 拼接提示信息 -> 抛出供上层捕获。
    决策：
    问题：通用 `ImportError` 难以定位具体缺失依赖。
    方案：自定义异常并附带可执行安装指引。
    代价：需要维护安装命令字符串。
    重评：当依赖检查统一由运行时诊断模块处理时。
    """

    def __init__(self, dependency_name: str, install_command: str):
        """初始化异常并拼接可读错误信息。

        契约：`dependency_name` 与 `install_command` 将在异常消息中暴露给调用方。
        关键路径：记录依赖名与安装命令 -> 初始化父类异常。
        决策：
        问题：依赖缺失信息需要可操作的安装提示。
        方案：在异常初始化阶段拼接完整消息。
        代价：错误信息固定格式，灵活性较低。
        重评：当引入统一错误码与本地化消息时。
        """
        self.dependency_name = dependency_name
        self.install_command = install_command
        super().__init__(f"{dependency_name} is not correctly installed. {install_command}")


def extract_docling_documents(
    data_inputs: Data | list[Data] | DataFrame, doc_key: str
) -> tuple[list[DoclingDocument], str | None]:
    """从输入中提取 `DoclingDocument` 列表，并返回可选告警信息。

    契约：支持 `Data`/`DataFrame`/列表；成功返回 `(documents, warning_message)`。
    关键路径：
    1) DataFrame：优先匹配 `doc_key` 列；
    2) 未命中时扫描列中是否存在 `DoclingDocument`；
    3) 非 DataFrame 则从 `Data.data[doc_key]` 提取。
    异常流：无法提取或类型不匹配时抛 `TypeError`。
    排障入口：当列名不匹配时会打 `logger.warning`。
    决策：
    问题：Docling 输出在不同管线中字段名可能不一致。
    方案：提供列名回退与告警提示，避免静默失败。
    代价：扫描列会增加少量开销。
    重评：当 Docling 输出字段标准化时。
    """
    documents: list[DoclingDocument] = []
    warning_message: str | None = None

    if isinstance(data_inputs, DataFrame):
        if not len(data_inputs):
            msg = "DataFrame is empty"
            raise TypeError(msg)

        # 实现：优先按 `doc_key` 精确匹配列名。
        if doc_key in data_inputs.columns:
            try:
                documents = data_inputs[doc_key].tolist()
            except Exception as e:
                msg = f"Error extracting DoclingDocument from DataFrame column '{doc_key}': {e}"
                raise TypeError(msg) from e
        else:
            # 注意：列名不匹配时扫描包含 `DoclingDocument` 的列。
            found_column = None
            for col in data_inputs.columns:
                try:
                    # 实现：抽样一条非空值判断是否为 `DoclingDocument`。
                    sample = data_inputs[col].dropna().iloc[0] if len(data_inputs[col].dropna()) > 0 else None
                    if sample is not None and isinstance(sample, DoclingDocument):
                        found_column = col
                        break
                except (IndexError, AttributeError):
                    continue

            if found_column:
                warning_message = (
                    f"Column '{doc_key}' not found, but found DoclingDocument objects in column '{found_column}'. "
                    f"Using '{found_column}' instead. Consider updating the 'Doc Key' parameter."
                )
                logger.warning(warning_message)
                try:
                    documents = data_inputs[found_column].tolist()
                except Exception as e:
                    msg = f"Error extracting DoclingDocument from DataFrame column '{found_column}': {e}"
                    raise TypeError(msg) from e
            else:
                # 排障：提供明确的替代方案提示，减少用户猜测成本。
                available_columns = list(data_inputs.columns)
                msg = (
                    f"Column '{doc_key}' not found in DataFrame. "
                    f"Available columns: {available_columns}. "
                    f"\n\nPossible solutions:\n"
                    f"1. Use the 'Data' output from Docling component instead of 'DataFrame' output\n"
                    f"2. Update the 'Doc Key' parameter to match one of the available columns\n"
                    f"3. If using VLM pipeline, try using the standard pipeline"
                )
                raise TypeError(msg)
    else:
        if not data_inputs:
            msg = "No data inputs provided"
            raise TypeError(msg)

        if isinstance(data_inputs, Data):
            if doc_key not in data_inputs.data:
                msg = (
                    f"'{doc_key}' field not available in the input Data. "
                    "Check that your input is a DoclingDocument. "
                    "You can use the Docling component to convert your input to a DoclingDocument."
                )
                raise TypeError(msg)
            documents = [data_inputs.data[doc_key]]
        else:
            try:
                documents = [
                    input_.data[doc_key]
                    for input_ in data_inputs
                    if isinstance(input_, Data)
                    and doc_key in input_.data
                    and isinstance(input_.data[doc_key], DoclingDocument)
                ]
                if not documents:
                    msg = f"No valid Data inputs found in {type(data_inputs)}"
                    raise TypeError(msg)
            except AttributeError as e:
                msg = f"Invalid input type in collection: {e}"
                raise TypeError(msg) from e
    return documents, warning_message


def _unwrap_secrets(obj):
    """递归展开 `SecretStr`，将密文替换为明文。

    契约：输入可为 `SecretStr`/dict/list/其他；输出结构保持不变。
    决策：
    问题：Pydantic 的 `SecretStr` 默认不可序列化为真实值。
    方案：递归解包并取出 `get_secret_value()`。
    代价：明文会进入内存，需避免持久化。
    重评：当引入专用密钥管理或加密序列化时。
    """
    if isinstance(obj, SecretStr):
        return obj.get_secret_value()
    if isinstance(obj, dict):
        return {k: _unwrap_secrets(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_unwrap_secrets(v) for v in obj]
    return obj


def _dump_with_secrets(model: BaseModel):
    """将 Pydantic 模型导出为可序列化字典并展开密钥。

    契约：返回 Python 原生结构；包含 `SecretStr` 的字段将变为明文。
    决策：
    问题：模型配置需跨进程传递但包含密钥字段。
    方案：先 `model_dump` 再 `_unwrap_secrets`。
    代价：密钥以明文形式短暂存在。
    重评：当支持安全的跨进程密钥传递时。
    """
    return _unwrap_secrets(model.model_dump(mode="python", round_trip=True))


def _serialize_pydantic_model(model: BaseModel):
    """将 Pydantic 模型序列化为可重建的字典。

    契约：返回包含 `__class_path__` 与 `config` 的字典。
    决策：
    问题：需要在 worker 中重建配置对象。
    方案：序列化类路径 + 配置数据。
    代价：重建时依赖 import 路径稳定。
    重评：当改用显式 schema 或注册表时。
    """
    return {
        "__class_path__": f"{model.__class__.__module__}.{model.__class__.__name__}",
        "config": _dump_with_secrets(model),
    }


def _deserialize_pydantic_model(data: dict):
    """从序列化字典重建 Pydantic 模型实例。

    契约：`data` 必须包含 `__class_path__` 与 `config`。
    异常流：类路径无效会触发 `ImportError`/`AttributeError`。
    决策：
    问题：跨进程需要恢复原始模型类型。
    方案：动态 import + `TypeAdapter.validate_python`。
    代价：运行期反射有一定开销。
    重评：当改用静态 schema 映射时。
    """
    module_name, class_name = data["__class_path__"].rsplit(".", 1)
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name)
    adapter = TypeAdapter(cls)
    return adapter.validate_python(data["config"])


# 注意：`DocumentConverter` 全局缓存，跨线程与多次调用复用以降低加载成本。
@lru_cache(maxsize=4)
def _get_cached_converter(
    pipeline: str,
    ocr_engine: str,
    *,
    do_picture_classification: bool,
    pic_desc_config_hash: str | None,
):
    """按配置创建并缓存 `DocumentConverter`。

    契约：`pipeline` 仅支持 `"standard"`/`"vlm"`；返回可复用转换器。
    关键路径：
    1) 根据 `pipeline` 构建 PDF/IMAGE 选项；
    2) 配置 OCR 与图片分类；
    3) 通过 LRU 缓存复用实例。
    异常流：未知 `pipeline` 抛 `ValueError`。
    性能瓶颈：首次加载模型可能耗时 15-20 分钟。
    决策：
    问题：模型加载成本高，重复初始化浪费时间。
    方案：使用 `lru_cache` 复用转换器。
    代价：内存占用上升，配置变更需注意缓存键。
    重评：当支持外部缓存或模型服务化时。
    """
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import OcrOptions, PdfPipelineOptions, VlmPipelineOptions
    from docling.document_converter import DocumentConverter, FormatOption, PdfFormatOption
    from docling.models.factories import get_ocr_factory
    from docling.pipeline.vlm_pipeline import VlmPipeline

    logger.info(f"Creating DocumentConverter for pipeline={pipeline}, ocr_engine={ocr_engine}")

    # 实现：标准管线配置，OCR 按需启用。
    def _get_standard_opts() -> PdfPipelineOptions:
        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = ocr_engine not in {"", "None"}
        if pipeline_options.do_ocr:
            ocr_factory = get_ocr_factory(
                allow_external_plugins=False,
            )
            ocr_options: OcrOptions = ocr_factory.create_options(
                kind=ocr_engine,
            )
            pipeline_options.ocr_options = ocr_options

        pipeline_options.do_picture_classification = do_picture_classification

        # 注意：`pic_desc_config_hash` 仅用于缓存键；描述配置在非缓存路径处理。
        _ = pic_desc_config_hash  # 注意：显式占位，避免静态检查误报。

        return pipeline_options

    # 实现：VLM 管线配置（当前无额外参数）。
    def _get_vlm_opts() -> VlmPipelineOptions:
        return VlmPipelineOptions()

    if pipeline == "standard":
        pdf_format_option = PdfFormatOption(
            pipeline_options=_get_standard_opts(),
        )
    elif pipeline == "vlm":
        pdf_format_option = PdfFormatOption(pipeline_cls=VlmPipeline, pipeline_options=_get_vlm_opts())
    else:
        msg = f"Unknown pipeline: {pipeline!r}"
        raise ValueError(msg)

    format_options: dict[InputFormat, FormatOption] = {
        InputFormat.PDF: pdf_format_option,
        InputFormat.IMAGE: pdf_format_option,
    }

    return DocumentConverter(format_options=format_options)


def docling_worker(
    *,
    file_paths: list[str],
    queue,
    pipeline: str,
    ocr_engine: str,
    do_picture_classification: bool,
    pic_desc_config: dict | None,
    pic_desc_prompt: str,
):
    """Docling worker：批量处理文件并将结果回传主进程。

    契约：输入文件路径列表与 `queue`；输出为处理结果列表或错误信息字典。
    关键路径：
    1) 注册信号处理并加载依赖；
    2) 获取缓存或非缓存的 `DocumentConverter`；
    3) 逐文件转换并将结果写入 `queue`。
    异常流：依赖缺失/解析失败会通过 `queue` 返回错误；支持中断优雅退出。
    性能瓶颈：首次模型加载可能耗时 15-20 分钟。
    排障入口：日志关键字 `Initializing`、`Processing file`、`Error processing file`。
    决策：
    问题：Docling 模型加载耗时高且需要隔离在 worker 进程。
    方案：使用全局缓存并在 worker 内集中处理。
    代价：缓存与配置存在限制（如 pic_desc_config 不可缓存）。
    重评：当 Docling 支持外部服务化或共享模型池时。
    """
    # 注意：注册信号以支持优雅退出，避免模型加载中断后残留进程。
    shutdown_requested = False

    def signal_handler(signum: int, frame) -> None:  # noqa: ARG001
        """处理终止信号并触发优雅退出。"""
        nonlocal shutdown_requested
        signal_names: dict[int, str] = {signal.SIGTERM: "SIGTERM", signal.SIGINT: "SIGINT"}
        signal_name = signal_names.get(signum, f"signal {signum}")

        logger.debug(f"Docling worker received {signal_name}, initiating graceful shutdown...")
        shutdown_requested = True

        # 排障：向主进程回传关闭原因，便于提示用户中断来源。
        with suppress(Exception):
            queue.put({"error": f"Worker interrupted by {signal_name}", "shutdown": True})

        # 注意：显式退出避免阻塞在后续耗时路径。
        sys.exit(0)

    def check_shutdown() -> None:
        """检查是否请求退出，必要时提前终止。"""
        if shutdown_requested:
            logger.info("Shutdown requested, exiting worker...")

            with suppress(Exception):
                queue.put({"error": "Worker shutdown requested", "shutdown": True})

            sys.exit(0)

    # 实现：尽早注册信号处理，避免导入阶段无法响应终止。
    try:
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
        logger.debug("Signal handlers registered for graceful shutdown")
    except (OSError, ValueError) as e:
        # 注意：部分平台不支持某些信号，记录后继续。
        logger.warning(f"Warning: Could not register signal handlers: {e}")

    # 注意：重依赖导入前检查是否已请求退出。
    check_shutdown()

    try:
        from docling.datamodel.base_models import ConversionStatus, InputFormat  # noqa: F401
        from docling.datamodel.pipeline_options import OcrOptions, PdfPipelineOptions, VlmPipelineOptions  # noqa: F401
        from docling.document_converter import DocumentConverter, FormatOption, PdfFormatOption  # noqa: F401
        from docling.models.factories import get_ocr_factory  # noqa: F401
        from docling.pipeline.vlm_pipeline import VlmPipeline  # noqa: F401
        from langchain_docling.picture_description import PictureDescriptionLangChainOptions  # noqa: F401

        # 注意：导入完成后再次检查退出请求。
        check_shutdown()
        logger.debug("Docling dependencies loaded successfully")

    except ModuleNotFoundError:
        msg = (
            "Docling is an optional dependency of Langflow. "
            "Install with `uv pip install 'langflow[docling]'` "
            "or refer to the documentation"
        )
        queue.put({"error": msg})
        return
    except ImportError as e:
        # 排障：保留具体依赖错误信息，避免被泛化提示覆盖。
        queue.put({"error": f"Failed to import a Docling dependency: {e}"})
        return
    except KeyboardInterrupt:
        logger.warning("KeyboardInterrupt during imports, exiting...")
        queue.put({"error": "Worker interrupted during imports", "shutdown": True})
        return

    # 性能：优先使用缓存转换器，避免 15-20 分钟模型重复加载。
    def _get_converter() -> DocumentConverter:
        check_shutdown()  # 注意：进入耗时路径前先检查退出。

        # 注意：图片描述配置暂不缓存（序列化复杂度高）。
        if pic_desc_config:
            logger.warning(
                "Picture description with LLM is not yet supported with cached converters. "
                "Using non-cached converter for this request."
            )
            # 注意：有 `pic_desc_config` 时回退到非缓存路径。
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, FormatOption, PdfFormatOption
            from docling.models.factories import get_ocr_factory
            from langchain_docling.picture_description import PictureDescriptionLangChainOptions

            pipeline_options = PdfPipelineOptions()
            pipeline_options.do_ocr = ocr_engine not in {"", "None"}
            if pipeline_options.do_ocr:
                ocr_factory = get_ocr_factory(allow_external_plugins=False)
                ocr_options = ocr_factory.create_options(kind=ocr_engine)
                pipeline_options.ocr_options = ocr_options

            pipeline_options.do_picture_classification = do_picture_classification
            pic_desc_llm = _deserialize_pydantic_model(pic_desc_config)
            logger.info("Docling enabling the picture description stage.")
            pipeline_options.do_picture_description = True
            pipeline_options.allow_external_plugins = True
            pipeline_options.picture_description_options = PictureDescriptionLangChainOptions(
                llm=pic_desc_llm,
                prompt=pic_desc_prompt,
            )

            pdf_format_option = PdfFormatOption(pipeline_options=pipeline_options)
            format_options: dict[InputFormat, FormatOption] = {
                InputFormat.PDF: pdf_format_option,
                InputFormat.IMAGE: pdf_format_option,
            }
            return DocumentConverter(format_options=format_options)

        # 性能：首次创建并缓存（15-20 分钟），后续复用（秒级）。
        pic_desc_config_hash = None  # 注意：此处固定为 None，已在上方排除配置。
        return _get_cached_converter(
            pipeline=pipeline,
            ocr_engine=ocr_engine,
            do_picture_classification=do_picture_classification,
            pic_desc_config_hash=pic_desc_config_hash,
        )

    try:
        # 注意：创建转换器前检查退出，避免卡在耗时初始化。
        check_shutdown()
        logger.info(f"Initializing {pipeline} pipeline with OCR: {ocr_engine or 'disabled'}")

        converter = _get_converter()

        # 注意：进入处理前再检查退出。
        check_shutdown()
        logger.info(f"Starting to process {len(file_paths)} files...")

        # 实现：逐文件处理并穿插退出检查。
        results = []
        for i, file_path in enumerate(file_paths):
            # 注意：每个文件处理前检查退出。
            check_shutdown()

            logger.debug(f"Processing file {i + 1}/{len(file_paths)}: {file_path}")

            try:
                single_result = converter.convert_all([file_path])
                results.extend(single_result)
                check_shutdown()

            except ImportError as import_error:
                # 排障：将 `ImportError` 原样传递给主进程处理。
                queue.put(
                    {"error": str(import_error), "error_type": "import_error", "original_exception": "ImportError"}
                )
                return

            except (OSError, ValueError, RuntimeError) as file_error:
                error_msg = str(file_error)

                # 排障：识别依赖缺失的典型错误信息并标注依赖名。
                dependency_name = None
                if "ocrmac is not correctly installed" in error_msg:
                    dependency_name = "ocrmac"
                elif "easyocr" in error_msg and "not installed" in error_msg:
                    dependency_name = "easyocr"
                elif "tesserocr" in error_msg and "not installed" in error_msg:
                    dependency_name = "tesserocr"
                elif "rapidocr" in error_msg and "not installed" in error_msg:
                    dependency_name = "rapidocr"

                if dependency_name:
                    queue.put(
                        {
                            "error": error_msg,
                            "error_type": "dependency_error",
                            "dependency_name": dependency_name,
                            "original_exception": type(file_error).__name__,
                        }
                    )
                    return

                # 注意：非依赖错误记录日志并继续处理其他文件。
                logger.error(f"Error processing file {file_path}: {file_error}")
                check_shutdown()

            except Exception as file_error:  # noqa: BLE001
                logger.error(f"Unexpected error processing file {file_path}: {file_error}")
                check_shutdown()

        # 注意：发送结果前进行最后一次退出检查。
        check_shutdown()

        # 实现：保持结果结构，与输入文件一一对应。
        processed_data = [
            {"document": res.document, "file_path": str(res.input.file), "status": res.status.name}
            if res.status == ConversionStatus.SUCCESS
            else None
            for res in results
        ]

        logger.info(f"Successfully processed {len([d for d in processed_data if d])} files")
        queue.put(processed_data)

    except KeyboardInterrupt:
        logger.warning("KeyboardInterrupt during processing, exiting gracefully...")
        queue.put({"error": "Worker interrupted during processing", "shutdown": True})
        return
    except Exception as e:  # noqa: BLE001
        if shutdown_requested:
            logger.exception("Exception occurred during shutdown, exiting...")
            return

        # 排障：将异常与 traceback 回传主进程。
        error_info = {"error": str(e), "traceback": traceback.format_exc()}
        logger.error(f"Error in worker: {error_info}")
        queue.put(error_info)
    finally:
        logger.info("Docling worker finishing...")
        # 注意：避免 worker 悬挂，统一记录退出路径。
        if shutdown_requested:
            logger.debug("Worker shutdown completed")
        else:
            logger.debug("Worker completed normally")
