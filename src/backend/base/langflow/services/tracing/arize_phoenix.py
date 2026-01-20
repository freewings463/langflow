"""
模块名称：Arize/Phoenix Tracer 适配

本模块实现 Arize Phoenix 的 tracing 适配，并维护 Langflow 的 root/child spans。
主要功能包括：
- 初始化 Phoenix/Arize OTEL tracer
- 记录组件级 spans 与日志/错误
- 将 Langflow 数据类型转换为 Phoenix 兼容结构

关键组件：
- `ArizePhoenixTracer`
- `CollectingSpanProcessor`

设计背景：对接 Arize/Phoenix 以提供可观测性与模型追踪能力。
注意事项：未配置 API Key/Space ID 时会自动禁用。
"""

from __future__ import annotations

import json
import math
import os
import threading
import traceback
import types
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from langchain_core.documents import Document
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from lfx.log.logger import logger
from lfx.schema.data import Data
from openinference.semconv.trace import OpenInferenceMimeTypeValues, SpanAttributes
from opentelemetry.sdk.trace.export import SpanProcessor
from opentelemetry.semconv.trace import SpanAttributes as OTELSpanAttributes
from opentelemetry.trace import Span, Status, StatusCode, use_span
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from typing_extensions import override

from langflow.schema.message import Message
from langflow.services.tracing.base import BaseTracer

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from langchain.callbacks.base import BaseCallbackHandler
    from lfx.graph.vertex.base import Vertex
    from opentelemetry.propagators.textmap import CarrierT
    from opentelemetry.util.types import AttributeValue

    from langflow.services.tracing.schema import Log


class CollectingSpanProcessor(SpanProcessor):
    def __init__(self):
        self.correlation_id = None
        self._lock = threading.Lock()

    def on_start(self, span, parent_context=None):
        """在特定 span 上注入关联 ID。"""
        _ = parent_context

        # 注意：只生成一次 correlation_id，保证跨 span 一致
        with self._lock:
            if self.correlation_id is None:
                self.correlation_id = str(uuid.uuid4())

        # 注意：仅在链路与模型 span 注入关联 ID
        if span.name in ("Langflow", "Language Model"):
            span.set_attribute("langflow.correlation_id", self.correlation_id)

    def on_end(self, span):
        pass

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis=30000):
        pass


class ArizePhoenixTracer(BaseTracer):
    flow_name: str
    flow_id: str
    chat_input_value: str
    chat_output_value: str

    def __init__(
        self, trace_name: str, trace_type: str, project_name: str, trace_id: UUID, session_id: str | None = None
    ):
        """初始化 Arize/Phoenix tracer 并创建 root span。

        契约：初始化失败时 `ready=False`。
        副作用：创建 root span 并注入 trace 上下文。
        失败语义：环境或 SDK 初始化失败将禁用 tracer。

        决策：root span 使用固定名称 `Langflow`
        问题：需要统一根 span 便于检索
        方案：固定名称并写入 `langflow.*` 属性
        代价：不同流程共用名称需依赖属性区分
        重评：若要求每条流独立 root 名称再调整
        """
        self.trace_name = trace_name
        self.trace_type = trace_type
        self.project_name = project_name
        self.trace_id = trace_id
        self.session_id = session_id
        self.flow_name = trace_name.split(" - ")[0]
        self.flow_id = trace_name.split(" - ")[-1]
        self.chat_input_value = ""
        self.chat_output_value = ""

        try:
            self._ready = self.setup_arize_phoenix()
            if not self._ready:
                return

            self.tracer = self.tracer_provider.get_tracer(__name__)
            self.propagator = TraceContextTextMapPropagator()
            self.carrier: dict[Any, CarrierT] = {}

            self.root_span = self.tracer.start_span(
                name="Langflow",
                start_time=self._get_current_timestamp(),
            )
            self.root_span.set_attribute(SpanAttributes.SESSION_ID, self.session_id or self.flow_id)
            self.root_span.set_attribute(SpanAttributes.OPENINFERENCE_SPAN_KIND, self.trace_type)
            self.root_span.set_attribute("langflow.trace_name", self.trace_name)
            self.root_span.set_attribute("langflow.trace_type", self.trace_type)
            self.root_span.set_attribute("langflow.project_name", self.project_name)
            self.root_span.set_attribute("langflow.trace_id", str(self.trace_id))
            self.root_span.set_attribute("langflow.session_id", str(self.session_id))
            self.root_span.set_attribute("langflow.flow_name", self.flow_name)
            self.root_span.set_attribute("langflow.flow_id", self.flow_id)

            with use_span(self.root_span, end_on_exit=False):
                self.propagator.inject(carrier=self.carrier)

            self.child_spans: dict[str, Span] = {}

        except Exception as e:  # noqa: BLE001
            logger.error("[Arize/Phoenix] Error Setting Up Tracer: %s", str(e), exc_info=True)
            self._ready = False

    @property
    def ready(self):
        """指示 tracer 是否可用。"""
        return self._ready

    def setup_arize_phoenix(self) -> bool:
        """配置 Arize/Phoenix 环境并注册 tracer provider。

        契约：初始化成功返回 `True`。
        失败语义：缺少依赖或鉴权失败时返回 `False`。
        """
        arize_phoenix_batch = os.getenv("ARIZE_PHOENIX_BATCH", "False").lower() in {
            "true",
            "t",
            "yes",
            "y",
            "1",
        }

        # 注意：Arize 与 Phoenix 可同时启用
        arize_api_key = os.getenv("ARIZE_API_KEY", None)
        arize_space_id = os.getenv("ARIZE_SPACE_ID", None)
        arize_collector_endpoint = os.getenv("ARIZE_COLLECTOR_ENDPOINT", "https://otlp.arize.com")
        enable_arize_tracing = bool(arize_api_key and arize_space_id)
        arize_endpoint = f"{arize_collector_endpoint}/v1"
        arize_headers = {
            "api_key": arize_api_key,
            "space_id": arize_space_id,
            "authorization": f"Bearer {arize_api_key}",
        }

        phoenix_api_key = os.getenv("PHOENIX_API_KEY", None)
        phoenix_collector_endpoint = os.getenv("PHOENIX_COLLECTOR_ENDPOINT", "https://app.phoenix.arize.com")
        phoenix_auth_disabled = "localhost" in phoenix_collector_endpoint or "127.0.0.1" in phoenix_collector_endpoint
        enable_phoenix_tracing = bool(phoenix_api_key) or phoenix_auth_disabled
        phoenix_endpoint = f"{phoenix_collector_endpoint}/v1/traces"
        phoenix_headers = (
            {
                "api_key": phoenix_api_key,
                "authorization": f"Bearer {phoenix_api_key}",
            }
            if phoenix_api_key
            else {}
        )

        if not (enable_arize_tracing or enable_phoenix_tracing):
            return False

        try:
            from phoenix.otel import (
                PROJECT_NAME,
                BatchSpanProcessor,
                GRPCSpanExporter,
                HTTPSpanExporter,
                Resource,
                SimpleSpanProcessor,
                TracerProvider,
            )

            name_without_space = self.flow_name.replace(" ", "-")
            project_name = self.project_name if name_without_space == "None" else name_without_space
            attributes = {PROJECT_NAME: project_name, "model_id": project_name}
            resource = Resource.create(attributes=attributes)
            tracer_provider = TracerProvider(resource=resource, verbose=False)
            span_processor = BatchSpanProcessor if arize_phoenix_batch else SimpleSpanProcessor

            if enable_arize_tracing:
                tracer_provider.add_span_processor(
                    span_processor=span_processor(
                        span_exporter=GRPCSpanExporter(endpoint=arize_endpoint, headers=arize_headers),
                    )
                )

            if enable_phoenix_tracing:
                tracer_provider.add_span_processor(
                    span_processor=span_processor(
                        span_exporter=HTTPSpanExporter(
                            endpoint=phoenix_endpoint,
                            headers=phoenix_headers,
                        ),
                    )
                )

            tracer_provider.add_span_processor(CollectingSpanProcessor())
            self.tracer_provider = tracer_provider
        except ImportError:
            logger.exception(
                "[Arize/Phoenix] Could not import Arize Phoenix OTEL packages."
                "Please install it with `pip install arize-phoenix-otel`."
            )
            return False

        try:
            from openinference.instrumentation.langchain import LangChainInstrumentor

            LangChainInstrumentor().instrument(tracer_provider=self.tracer_provider, skip_dep_check=True)
        except ImportError:
            logger.exception(
                "[Arize/Phoenix] Could not import LangChainInstrumentor."
                "Please install it with `pip install openinference-instrumentation-langchain`."
            )
            return False

        return True

    @override
    def add_trace(
        self,
        trace_id: str,
        trace_name: str,
        trace_type: str,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        vertex: Vertex | None = None,
    ) -> None:
        """创建组件级 span 并写入输入与元数据。

        契约：`trace_id` 唯一标识组件 span。
        副作用：向 tracer 写入 span 属性。
        失败语义：未就绪时静默返回。
        """
        if not self._ready:
            return

        span_context = self.propagator.extract(carrier=self.carrier)
        child_span = self.tracer.start_span(
            name=trace_name,
            context=span_context,
            start_time=self._get_current_timestamp(),
        )

        if trace_type == "prompt":
            child_span.set_attribute(SpanAttributes.OPENINFERENCE_SPAN_KIND, "chain")
        else:
            child_span.set_attribute(SpanAttributes.OPENINFERENCE_SPAN_KIND, trace_type)

        processed_inputs = self._convert_to_arize_phoenix_types(inputs) if inputs else {}
        if processed_inputs:
            child_span.set_attribute(SpanAttributes.INPUT_VALUE, self._safe_json_dumps(processed_inputs))
            child_span.set_attribute(SpanAttributes.INPUT_MIME_TYPE, OpenInferenceMimeTypeValues.JSON.value)

        processed_metadata = self._convert_to_arize_phoenix_types(metadata) if metadata else {}
        if processed_metadata:
            for key, value in processed_metadata.items():
                child_span.set_attribute(f"{SpanAttributes.METADATA}.{key}", value)

        if vertex and vertex.id is not None:
            child_span.set_attribute("vertex_id", vertex.id)

        component_name = trace_id.split("-")[0]
        if component_name == "ChatInput":
            self.chat_input_value = processed_inputs["input_value"]
        elif component_name == "ChatOutput":
            self.chat_output_value = processed_inputs["input_value"]

        self.child_spans[trace_id] = child_span

    @override
    def end_trace(
        self,
        trace_id: str,
        trace_name: str,
        outputs: dict[str, Any] | None = None,
        error: Exception | None = None,
        logs: Sequence[Log | dict] = (),
    ) -> None:
        """结束组件级 span 并写入输出/日志/错误。

        契约：仅结束已存在的 child span。
        副作用：结束 span 并移除缓存。
        失败语义：未就绪或 span 不存在时静默返回。
        """
        if not self._ready or trace_id not in self.child_spans:
            return

        child_span = self.child_spans[trace_id]

        processed_outputs = self._convert_to_arize_phoenix_types(outputs) if outputs else {}
        if processed_outputs:
            child_span.set_attribute(SpanAttributes.OUTPUT_VALUE, self._safe_json_dumps(processed_outputs))
            child_span.set_attribute(SpanAttributes.OUTPUT_MIME_TYPE, OpenInferenceMimeTypeValues.JSON.value)

        logs_dicts = [log if isinstance(log, dict) else log.model_dump() for log in logs]
        processed_logs = (
            self._convert_to_arize_phoenix_types({log.get("name"): log for log in logs_dicts}) if logs else {}
        )
        if processed_logs:
            child_span.set_attribute("logs", self._safe_json_dumps(processed_logs))

        self._set_span_status(child_span, error)
        child_span.end(end_time=self._get_current_timestamp())
        self.child_spans.pop(trace_id)

    @override
    def end(
        self,
        inputs: dict[str, Any],
        outputs: dict[str, Any],
        error: Exception | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """结束 root span 并写入输入/输出与元数据。

        契约：使用 ChatInput/ChatOutput 作为最终输入输出。
        副作用：结束 root span 并注销 LangChain instrumentor。
        失败语义：未就绪时静默返回。
        """
        if not self._ready:
            return

        if self.root_span:
            self.root_span.set_attribute(SpanAttributes.INPUT_VALUE, self.chat_input_value)
            self.root_span.set_attribute(SpanAttributes.INPUT_MIME_TYPE, OpenInferenceMimeTypeValues.TEXT.value)
            self.root_span.set_attribute(SpanAttributes.OUTPUT_VALUE, self.chat_output_value)
            self.root_span.set_attribute(SpanAttributes.OUTPUT_MIME_TYPE, OpenInferenceMimeTypeValues.TEXT.value)

            processed_metadata = self._convert_to_arize_phoenix_types(metadata) if metadata else {}
            if processed_metadata:
                for key, value in processed_metadata.items():
                    self.root_span.set_attribute(f"{SpanAttributes.METADATA}.{key}", value)

            self._set_span_status(self.root_span, error)
            self.root_span.end(end_time=self._get_current_timestamp())
        try:
            from openinference.instrumentation.langchain import LangChainInstrumentor

            LangChainInstrumentor().uninstrument(tracer_provider=self.tracer_provider, skip_dep_check=True)
        except ImportError:
            logger.exception(
                "[Arize/Phoenix] Could not import LangChainInstrumentor."
                "Please install it with `pip install openinference-instrumentation-langchain`."
            )

    def _convert_to_arize_phoenix_types(self, io_dict: dict[str | Any, Any]) -> dict[str, Any]:
        """将输入字典转换为 Arize/Phoenix 兼容格式。"""
        return {
            str(key): self._convert_to_arize_phoenix_type(value) for key, value in io_dict.items() if key is not None
        }

    def _convert_to_arize_phoenix_type(self, value):
        """递归转换为 Arize/Phoenix 兼容类型。"""
        if isinstance(value, dict):
            value = {key: self._convert_to_arize_phoenix_type(val) for key, val in value.items()}

        elif isinstance(value, list):
            value = [self._convert_to_arize_phoenix_type(v) for v in value]

        elif isinstance(value, Message):
            value = value.text

        elif isinstance(value, Data):
            value = value.get_text()

        elif isinstance(value, (BaseMessage | HumanMessage | SystemMessage)):
            value = value.content

        elif isinstance(value, Document):
            value = value.page_content

        elif isinstance(value, (types.GeneratorType | types.NoneType)):
            value = str(value)

        elif isinstance(value, float) and not math.isfinite(value):
            value = "NaN"

        return value

    @staticmethod
    def _error_to_string(error: Exception | None):
        """将异常转换为包含堆栈的字符串。"""
        error_message = None
        if error:
            string_stacktrace = traceback.format_exception(error)
            error_message = f"{error.__class__.__name__}: {error}\n\n{string_stacktrace}"
        return error_message

    @staticmethod
    def _get_current_timestamp() -> int:
        """获取 UTC 纳秒时间戳。"""
        return int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)

    @staticmethod
    def _safe_json_dumps(obj: Any, **kwargs: Any) -> str:
        """安全 JSON 序列化包装。"""
        return json.dumps(obj, default=str, ensure_ascii=False, **kwargs)

    def _set_span_status(self, current_span: Span, error: Exception | None = None):
        """根据错误设置 span 状态与属性。"""
        if error:
            error_string = self._error_to_string(error)
            current_span.set_status(Status(StatusCode.ERROR, error_string))
            current_span.set_attribute("error.message", error_string)

            if isinstance(error, Exception):
                current_span.record_exception(error)
            else:
                exception_type = error.__class__.__name__
                exception_message = str(error)
                if not exception_message:
                    exception_message = repr(error)
                attributes: dict[str, AttributeValue] = {
                    OTELSpanAttributes.EXCEPTION_TYPE: exception_type,
                    OTELSpanAttributes.EXCEPTION_MESSAGE: exception_message,
                    OTELSpanAttributes.EXCEPTION_ESCAPED: False,
                    OTELSpanAttributes.EXCEPTION_STACKTRACE: error_string,
                }
                current_span.add_event(name="exception", attributes=attributes)
        else:
            current_span.set_status(Status(StatusCode.OK))

    @override
    def get_langchain_callback(self) -> BaseCallbackHandler | None:
        """返回 LangChain callback（此实现不提供）。"""
        return None

    def close(self):
        """关闭前强制 flush tracer spans。

        契约：若 provider 支持 `force_flush` 则调用。
        失败语义：flush 失败记录日志但不中断。
        """
        try:
            if hasattr(self, "tracer_provider") and hasattr(self.tracer_provider, "force_flush"):
                self.tracer_provider.force_flush(timeout_millis=3000)
        except (ValueError, RuntimeError, OSError) as e:
            logger.error("[Arize/Phoenix] Error Flushing Spans: %s", str(e), exc_info=True)

    def __del__(self):
        """对象销毁前确保 flush。"""
        self.close()
