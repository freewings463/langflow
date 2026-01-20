"""
模块名称：docling_remote

本模块提供 Docling Serve 远程处理组件，通过 HTTP API 转换文档。
主要功能包括：
- 上传文档并轮询异步任务状态
- 解析返回的 DoclingDocument 并输出结果

关键组件：
- `DoclingRemoteComponent`：远程 Docling 处理组件

设计背景：云环境或无本地依赖时需要远程文档处理
使用场景：连接自建 Docling Serve 实例进行转换
注意事项：需配置可访问的服务 URL 与可选认证头
"""

import base64
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx
from docling_core.types.doc import DoclingDocument
from pydantic import ValidationError

from lfx.base.data import BaseFileComponent
from lfx.inputs import IntInput, NestedDictInput, StrInput
from lfx.inputs.inputs import FloatInput
from lfx.schema import Data
from lfx.utils.util import transform_localhost_url


class DoclingRemoteComponent(BaseFileComponent):
    """Docling Serve 远程处理组件。

    契约：`api_url` 指向 Docling Serve 实例；输出与输入文件一一对应。
    副作用：发起多次 HTTP 请求并轮询任务状态。
    失败语义：HTTP 错误抛异常，处理失败返回 None。
    决策：通过远程 Docling Serve 处理文档。
    问题：云环境无法安装本地 OCR/Docling 依赖。
    方案：调用远程服务完成解析与转换。
    代价：受网络与服务可用性影响。
    重评：当本地依赖可用或服务成本过高时。
    """
    display_name = "Docling Serve"
    description = "Uses Docling to process input documents connecting to your instance of Docling Serve."
    documentation = "https://docling-project.github.io/docling/"
    trace_type = "tool"
    icon = "Docling"
    name = "DoclingRemote"

    MAX_500_RETRIES = 5

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
        "jpg",
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
        StrInput(
            name="api_url",
            display_name="Server address",
            info="URL of the Docling Serve instance.",
            required=True,
        ),
        IntInput(
            name="max_concurrency",
            display_name="Concurrency",
            info="Maximum number of concurrent requests for the server.",
            advanced=True,
            value=2,
        ),
        FloatInput(
            name="max_poll_timeout",
            display_name="Maximum poll time",
            info="Maximum waiting time for the document conversion to complete.",
            advanced=True,
            value=3600,
        ),
        NestedDictInput(
            name="api_headers",
            display_name="HTTP headers",
            advanced=True,
            required=False,
            info=("Optional dictionary of additional headers required for connecting to Docling Serve."),
        ),
        NestedDictInput(
            name="docling_serve_opts",
            display_name="Docling options",
            advanced=True,
            required=False,
            info=(
                "Optional dictionary of additional options. "
                "See https://github.com/docling-project/docling-serve/blob/main/docs/usage.md for more information."
            ),
        ),
    ]

    outputs = [
        *BaseFileComponent.get_base_outputs(),
    ]

    def process_files(self, file_list: list[BaseFileComponent.BaseFile]) -> list[BaseFileComponent.BaseFile]:
        """处理文件并通过 Docling Serve 返回结果。

        契约：`file_list` 中需包含可读路径；输出与输入对齐。
        副作用：发起远程转换请求并轮询任务状态。
        失败语义：HTTP 异常会抛出；单个文件失败返回 None。
        关键路径（三步）：
        1) 规范化服务 URL 并构造基础地址。
        2) 并发提交任务并轮询状态。
        3) 解析 DoclingDocument 并回填结果。
        异常流：状态轮询 5xx 超限、超时或 JSON 结构异常。
        性能瓶颈：网络 RTT 与服务端解析耗时。
        排障入口：日志 `Docling remote processing failed` 与超时信息。
        决策：使用异步任务 + 轮询方式获取结果。
        问题：文档解析耗时较长且需要异步处理。
        方案：提交异步任务并定期查询状态。
        代价：需要额外轮询请求与等待时间。
        重评：当服务端支持回调或长连接推送时。
        """
        # 注意：容器内访问 localhost 需转换为可达地址。
        transformed_url = transform_localhost_url(self.api_url)
        base_url = f"{transformed_url}/v1"

        def _convert_document(client: httpx.Client, file_path: Path, options: dict[str, Any]) -> Data | None:
            """提交单文件转换并轮询结果。

            契约：返回 `Data` 或 `None`（失败/无结果）。
            副作用：对 Docling Serve 发起多次 HTTP 请求。
            失败语义：HTTP 状态异常抛出 `HTTPStatusError`。
            性能瓶颈：轮询间隔与服务端任务耗时。
            """
            encoded_doc = base64.b64encode(file_path.read_bytes()).decode()
            payload = {
                "options": options,
                "sources": [{"kind": "file", "base64_string": encoded_doc, "filename": file_path.name}],
            }

            response = client.post(f"{base_url}/convert/source/async", json=payload)
            response.raise_for_status()
            task = response.json()

            http_failures = 0
            retry_status_start = 500
            retry_status_end = 600
            start_wait_time = time.monotonic()
            while task["task_status"] not in ("success", "failure"):
                processing_time = time.monotonic() - start_wait_time
                if processing_time >= self.max_poll_timeout:
                    msg = (
                        f"Processing time {processing_time=} exceeds the maximum poll timeout {self.max_poll_timeout=}."
                        "Please increase the max_poll_timeout parameter or review why the processing "
                        "takes long on the server."
                    )
                    self.log(msg)
                    raise RuntimeError(msg)

                time.sleep(2)
                response = client.get(f"{base_url}/status/poll/{task['task_id']}")

                if retry_status_start <= response.status_code < retry_status_end:
                    http_failures += 1
                    if http_failures > self.MAX_500_RETRIES:
                        self.log(f"The status requests got a http response {response.status_code} too many times.")
                        return None
                    continue

                task = response.json()

            result_resp = client.get(f"{base_url}/result/{task['task_id']}")
            result_resp.raise_for_status()
            result = result_resp.json()

            if "json_content" not in result["document"] or result["document"]["json_content"] is None:
                self.log("No JSON DoclingDocument found in the result.")
                return None

            try:
                doc = DoclingDocument.model_validate(result["document"]["json_content"])
                return Data(data={"doc": doc, "file_path": str(file_path)})
            except ValidationError as e:
                self.log(f"Error validating the document. {e}")
                return None

        docling_options = {
            "to_formats": ["json"],
            "image_export_mode": "placeholder",
            **(self.docling_serve_opts or {}),
        }

        processed_data: list[Data | None] = []
        with (
            httpx.Client(headers=self.api_headers) as client,
            ThreadPoolExecutor(max_workers=self.max_concurrency) as executor,
        ):
            futures: list[tuple[int, Future]] = []
            for i, file in enumerate(file_list):
                if file.path is None:
                    processed_data.append(None)
                    continue

                futures.append((i, executor.submit(_convert_document, client, file.path, docling_options)))

            for _index, future in futures:
                try:
                    result_data = future.result()
                    processed_data.append(result_data)
                except (httpx.HTTPStatusError, httpx.RequestError, KeyError, ValueError) as exc:
                    self.log(f"Docling remote processing failed: {exc}")
                    raise

        return self.rollup_data(file_list, processed_data)
