"""
模块名称：docling_inline

本模块提供 Docling 本地处理组件，使用本地依赖对文件进行解析。
主要功能包括：
- 调用 Docling 本地模型处理文件
- 通过线程队列获取异步结果并回传

关键组件：
- `DoclingInlineComponent`：本地 Docling 处理组件

设计背景：需要在本地环境中直接调用 Docling 解析文档
使用场景：本地部署或具备 OCR 依赖的环境
注意事项：云环境可能缺少 OCR 依赖，应改用远程组件
"""

import queue
import threading
import time

from lfx.base.data import BaseFileComponent
from lfx.base.data.docling_utils import _serialize_pydantic_model, docling_worker
from lfx.inputs import BoolInput, DropdownInput, HandleInput, StrInput
from lfx.schema import Data


class DoclingInlineComponent(BaseFileComponent):
    """Docling 本地处理组件。

    契约：输入文件列表，输出携带 DoclingDocument 的 `Data`。
    副作用：启动工作线程并调用 Docling 本地模型。
    失败语义：缺少依赖抛 `ImportError`，处理失败抛 `RuntimeError`。
    决策：优先使用本地 Docling 依赖进行处理。
    问题：需要在离线/内网环境完成文档解析。
    方案：本地执行 Docling 与 OCR，避免外部服务依赖。
    代价：安装成本与资源占用较高。
    重评：当本地依赖不可用或成本过高时。
    """
    display_name = "Docling"
    description = "Uses Docling to process input documents running the Docling models locally."
    documentation = "https://docling-project.github.io/docling/"
    trace_type = "tool"
    icon = "Docling"
    name = "DoclingInline"

    # 注意：支持格式列表参考 Docling 官方文档。
    VALID_EXTENSIONS = [
        "adoc",
        "asciidoc",
        "asc",
        "bmp",
        "csv",
        "dotx",
        "dotm",
        "docm",
        "docx",
        "htm",
        "html",
        "jpeg",
        "json",
        "md",
        "pdf",
        "png",
        "potx",
        "ppsx",
        "pptm",
        "potm",
        "ppsm",
        "pptx",
        "tiff",
        "txt",
        "xls",
        "xlsx",
        "xhtml",
        "xml",
        "webp",
    ]

    inputs = [
        *BaseFileComponent.get_base_inputs(),
        DropdownInput(
            name="pipeline",
            display_name="Pipeline",
            info="Docling pipeline to use",
            options=["standard", "vlm"],
            value="standard",
        ),
        DropdownInput(
            name="ocr_engine",
            display_name="OCR Engine",
            info="OCR engine to use. None will disable OCR.",
            options=["None", "easyocr", "tesserocr", "rapidocr", "ocrmac"],
            value="None",
        ),
        BoolInput(
            name="do_picture_classification",
            display_name="Picture classification",
            info="If enabled, the Docling pipeline will classify the pictures type.",
            value=False,
        ),
        HandleInput(
            name="pic_desc_llm",
            display_name="Picture description LLM",
            info="If connected, the model to use for running the picture description task.",
            input_types=["LanguageModel"],
            required=False,
        ),
        StrInput(
            name="pic_desc_prompt",
            display_name="Picture description prompt",
            value="Describe the image in three sentences. Be concise and accurate.",
            info="The user prompt to use when invoking the model.",
            advanced=True,
        ),
        # 注意：后续可扩展更多 Docling 选项。
    ]

    outputs = [
        *BaseFileComponent.get_base_outputs(),
    ]

    def _wait_for_result_with_thread_monitoring(
        self, result_queue: queue.Queue, thread: threading.Thread, timeout: int = 300
    ):
        """等待结果并监控线程健康状态。

        契约：在 `timeout` 内返回结果或抛出异常。
        失败语义：线程提前退出且无结果抛 `RuntimeError`；超时抛 `TimeoutError`。
        关键路径（三步）：1) 检查线程状态 2) 轮询队列 3) 超时退出。
        性能瓶颈：轮询间隔与超时设置影响等待时长。
        排障入口：异常信息包含线程状态与超时秒数。
        """
        start_time = time.time()

        while time.time() - start_time < timeout:
            if not thread.is_alive():
                try:
                    result = result_queue.get_nowait()
                except queue.Empty:
                    msg = "Worker thread crashed unexpectedly without producing result."
                    raise RuntimeError(msg) from None
                else:
                    self.log("Thread completed and result retrieved")
                    return result

            try:
                result = result_queue.get(timeout=1)
            except queue.Empty:
                continue
            else:
                self.log("Result received from worker thread")
                return result

        msg = f"Thread timed out after {timeout} seconds"
        raise TimeoutError(msg)

    def _stop_thread_gracefully(self, thread: threading.Thread, timeout: int = 10):
        """等待线程自然结束（不强杀）。

        契约：线程已结束则直接返回。
        副作用：阻塞等待至超时。
        失败语义：线程超时仍存活仅记录日志。
        决策：不强制终止线程。
        问题：Python 线程无法安全强杀，易导致资源泄露。
        方案：使用 `join` 等待并记录超时告警。
        代价：超时后仍可能有后台线程存活。
        重评：当可迁移到可中断的任务执行框架时。
        """
        if not thread.is_alive():
            return

        self.log("Waiting for thread to complete gracefully")
        thread.join(timeout=timeout)

        if thread.is_alive():
            self.log("Warning: Thread still alive after timeout")

    def process_files(self, file_list: list[BaseFileComponent.BaseFile]) -> list[BaseFileComponent.BaseFile]:
        """处理文件并返回带 DoclingDocument 的数据。

        契约：`file_list` 中的文件路径必须存在；返回与输入对齐的输出列表。
        副作用：启动工作线程并调用本地 Docling 依赖。
        失败语义：依赖缺失抛 `ImportError`，处理异常原样抛出。
        关键路径（三步）：
        1) 校验依赖并提取文件路径。
        2) 启动线程执行 Docling 处理并等待结果。
        3) 解析结果并回填输出。
        异常流：依赖缺失、线程崩溃、超时或 OCR 依赖缺失。
        性能瓶颈：Docling 解析与 OCR 模型推理。
        排障入口：日志关键字 `Error during processing` 或异常信息。
        决策：使用线程而非多进程执行 Docling 任务。
        问题：多进程会导致缓存无法共享且内存占用大。
        方案：单进程多线程共享 `DocumentConverter` 缓存。
        代价：受 GIL 影响并发吞吐有限。
        重评：当处理量显著增大且可接受多进程开销时。
        """
        try:
            from docling.document_converter import DocumentConverter  # noqa: F401
        except ImportError as e:
            msg = (
                "Docling is an optional dependency. Install with `uv pip install 'langflow[docling]'` or refer to the "
                "documentation on how to install optional dependencies."
            )
            raise ImportError(msg) from e

        file_paths = [file.path for file in file_list if file.path]

        if not file_paths:
            self.log("No files to process.")
            return file_list

        pic_desc_config: dict | None = None
        if self.pic_desc_llm is not None:
            pic_desc_config = _serialize_pydantic_model(self.pic_desc_llm)

        # 注意：使用线程共享内存以复用全局 DocumentConverter 缓存。
        result_queue: queue.Queue = queue.Queue()
        thread = threading.Thread(
            target=docling_worker,
            kwargs={
                "file_paths": file_paths,
                "queue": result_queue,
                "pipeline": self.pipeline,
                "ocr_engine": self.ocr_engine,
                "do_picture_classification": self.do_picture_classification,
                "pic_desc_config": pic_desc_config,
                "pic_desc_prompt": self.pic_desc_prompt,
            },
            # 注意：允许线程在主线程退出后继续收尾，避免中途终止。
            daemon=False,
        )

        result = None
        thread.start()

        try:
            result = self._wait_for_result_with_thread_monitoring(result_queue, thread, timeout=300)
        except KeyboardInterrupt:
            self.log("Docling thread cancelled by user")
            result = []
        except Exception as e:
            self.log(f"Error during processing: {e}")
            raise
        finally:
            self._stop_thread_gracefully(thread)

        # 注意：对依赖缺失与中断场景进行细分处理。
        if isinstance(result, dict) and "error" in result:
            error_msg = result["error"]

            # 注意：OCR 依赖缺失单独提示安装方式。
            if result.get("error_type") == "dependency_error":
                dependency_name = result.get("dependency_name", "Unknown dependency")
                install_command = result.get("install_command", "Please check documentation")

                # 注意：拼装面向用户的安装提示。
                user_message = (
                    f"Missing OCR dependency: {dependency_name}. "
                    f"{install_command} "
                    f"Alternatively, you can set OCR Engine to 'None' to disable OCR processing."
                )
                raise ImportError(user_message)

            if error_msg.startswith("Docling is not installed"):
                raise ImportError(error_msg)

            if "Worker interrupted by SIGINT" in error_msg or "shutdown" in result:
                self.log("Docling process cancelled by user")
                result = []
            else:
                raise RuntimeError(error_msg)

        processed_data = [Data(data={"doc": r["document"], "file_path": r["file_path"]}) if r else None for r in result]
        return self.rollup_data(file_list, processed_data)
