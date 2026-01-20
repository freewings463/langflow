"""
模块名称：LangWatch Tracer 适配

本模块实现 LangWatch 的 tracing 适配，基于 OTEL 发送 spans。
主要功能包括：
- 初始化 LangWatch 客户端与专用 TracerProvider
- 记录组件级 span 并关联上下游
- 将 Langflow 数据类型转换为 LangWatch 兼容格式

关键组件：
- `LangWatchTracer`

设计背景：与 LangWatch 集成以获得可视化与调试能力。
注意事项：未配置 `LANGWATCH_API_KEY` 时自动禁用。
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, cast

import nanoid
from lfx.log.logger import logger
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from typing_extensions import override

from langflow.schema.data import Data
from langflow.services.tracing.base import BaseTracer

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from langchain.callbacks.base import BaseCallbackHandler
    from langwatch.tracer import ContextSpan
    from lfx.graph.vertex.base import Vertex

    from langflow.services.tracing.schema import Log


class LangWatchTracer(BaseTracer):
    flow_id: str
    tracer_provider = None

    def __init__(self, trace_name: str, trace_type: str, project_name: str, trace_id: UUID):
        """初始化 LangWatch tracer。

        契约：初始化失败时 `ready=False`。
        副作用：可能创建全局 TracerProvider 与 OTEL exporter。
        失败语义：SDK 导入失败或鉴权失败时禁用。
        """
        self.trace_name = trace_name
        self.trace_type = trace_type
        self.project_name = project_name
        self.trace_id = trace_id
        self.flow_id = trace_name.split(" - ")[-1]

        try:
            self._ready: bool = self.setup_langwatch()
            if not self._ready:
                return

            # Pass the dedicated tracer_provider here
            self.trace = self._client.trace(trace_id=str(self.trace_id), tracer_provider=self.tracer_provider)
            self.trace.__enter__()
            self.spans: dict[str, ContextSpan] = {}

            name_without_id = " - ".join(trace_name.split(" - ")[0:-1])
            name_without_id = project_name if name_without_id == "None" else name_without_id
            self.trace.root_span.update(
                # nanoid to make the span_id globally unique, which is required for LangWatch for now
                span_id=f"{self.flow_id}-{nanoid.generate(size=6)}",
                name=name_without_id,
                type="workflow",
            )
        except Exception:  # noqa: BLE001
            logger.debug("Error setting up LangWatch tracer")
            self._ready = False

    @property
    def ready(self):
        """指示 tracer 是否可用。"""
        return self._ready

    def setup_langwatch(self) -> bool:
        """配置 LangWatch SDK 与 OTEL exporter。"""
        if "LANGWATCH_API_KEY" not in os.environ:
            return False
        try:
            import langwatch
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            # Initialize the shared provider if it doesn't exist
            if self.tracer_provider is None:
                api_key = os.environ["LANGWATCH_API_KEY"]
                endpoint = os.environ.get("LANGWATCH_ENDPOINT", "https://app.langwatch.ai")

                resource = Resource.create(attributes={"service.name": "langflow"})
                exporter = OTLPSpanExporter(
                    endpoint=f"{endpoint}/api/otel/v1/traces", headers={"Authorization": f"Bearer {api_key}"}
                )
                provider = TracerProvider(resource=resource)
                provider.add_span_processor(BatchSpanProcessor(exporter))
                LangWatchTracer.tracer_provider = provider

                # 注意：跳过全局 OTEL 设置，避免影响 FastAPIInstrumentor
                langwatch.setup(
                    api_key=api_key,
                    endpoint_url=endpoint,
                    skip_open_telemetry_setup=True,
                )

            self._client = langwatch
        except ImportError as e:
            logger.exception(f"{e}")
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
        """创建组件级 span 并关联上下游节点。"""
        if not self._ready:
            return
        # 注意：session_id 与 flow_id 相同时不创建 thread_id
        if "session_id" in inputs and inputs["session_id"] != self.flow_id:
            self.trace.update(metadata=(self.trace.metadata or {}) | {"thread_id": inputs["session_id"]})

        name_without_id = " (".join(trace_name.split(" (")[0:-1])

        previous_nodes = (
            [span for key, span in self.spans.items() for edge in vertex.incoming_edges if key == edge.source_id]
            if vertex and len(vertex.incoming_edges) > 0
            else []
        )

        span = self.trace.span(
            # Add a nanoid to make the span_id globally unique, which is required for LangWatch for now
            span_id=f"{trace_id}-{nanoid.generate(size=6)}",
            name=name_without_id,
            type="component",
            parent=(previous_nodes[-1] if len(previous_nodes) > 0 else self.trace.root_span),
            input=self._convert_to_langwatch_types(inputs),
        )
        self.trace.set_current_span(span)
        self.spans[trace_id] = span

    @override
    def end_trace(
        self,
        trace_id: str,
        trace_name: str,
        outputs: dict[str, Any] | None = None,
        error: Exception | None = None,
        logs: Sequence[Log | dict] = (),
    ) -> None:
        """结束组件级 span 并写入输出/错误。"""
        if not self._ready:
            return
        if self.spans.get(trace_id):
            self.spans[trace_id].end(output=self._convert_to_langwatch_types(outputs), error=error)

    def end(
        self,
        inputs: dict[str, Any],
        outputs: dict[str, Any],
        error: Exception | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """结束根 span 并写入最终输入/输出。"""
        if not self._ready:
            return
        self.trace.root_span.end(
            input=self._convert_to_langwatch_types(inputs) if self.trace.root_span.input is None else None,
            output=self._convert_to_langwatch_types(outputs) if self.trace.root_span.output is None else None,
            error=error,
        )

        if metadata and "flow_name" in metadata:
            self.trace.update(metadata=(self.trace.metadata or {}) | {"labels": [f"Flow: {metadata['flow_name']}"]})

        if self.trace.api_key or self._client._api_key:
            try:
                self.trace.__exit__(None, None, None)
            except ValueError:  # ignoring token was created in a different Context errors
                return

    def _convert_to_langwatch_types(self, io_dict: dict[str, Any] | None):
        """批量转换为 LangWatch 兼容类型。"""
        from langwatch.utils import autoconvert_typed_values

        if io_dict is None:
            return None
        converted = {}
        for key, value in io_dict.items():
            converted[key] = self._convert_to_langwatch_type(value)
        return autoconvert_typed_values(converted)

    def _convert_to_langwatch_type(self, value):
        """递归转换为 LangWatch 兼容类型。"""
        from langchain_core.messages import BaseMessage
        from langwatch.langchain import langchain_message_to_chat_message, langchain_messages_to_chat_messages
        from lfx.schema.message import Message

        if isinstance(value, dict):
            value = {key: self._convert_to_langwatch_type(val) for key, val in value.items()}
        elif isinstance(value, list):
            value = [self._convert_to_langwatch_type(v) for v in value]
        elif isinstance(value, Message):
            if "prompt" in value:
                prompt = value.load_lc_prompt()
                if len(prompt.input_variables) == 0 and all(isinstance(m, BaseMessage) for m in prompt.messages):
                    value = langchain_messages_to_chat_messages([cast("list[BaseMessage]", prompt.messages)])
                else:
                    value = cast("dict", value.load_lc_prompt())
            elif value.sender:
                value = langchain_message_to_chat_message(value.to_lc_message())
            else:
                value = cast("dict", value.to_lc_document())
        elif isinstance(value, Data):
            value = cast("dict", value.to_lc_document())
        return value

    def get_langchain_callback(self) -> BaseCallbackHandler | None:
        """返回 LangChain callback（若可用）。"""
        if self.trace is None:
            return None

        return self.trace.get_langchain_callback()
