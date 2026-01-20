"""
模块名称：Opik Tracer 适配

本模块实现 Opik tracing 适配，并支持分布式 trace 头传递。
主要功能包括：
- 初始化 Opik 客户端并创建 trace
- 记录组件级 span 的输入/输出/日志
- 提供 LangChain 回调与分布式 header

关键组件：
- `OpikTracer`
- `get_distributed_trace_headers`

设计背景：在 Opik 上统一追踪 Langflow 运行链路。
注意事项：未配置 `OPIK_API_KEY`/`OPIK_URL_OVERRIDE` 时会禁用。
"""

from __future__ import annotations

import os
import types
from typing import TYPE_CHECKING, Any

from langchain_core.documents import Document
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from lfx.log.logger import logger
from typing_extensions import override

from langflow.schema.data import Data
from langflow.schema.message import Message
from langflow.services.tracing.base import BaseTracer

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from langchain.callbacks.base import BaseCallbackHandler
    from lfx.graph.vertex.base import Vertex

    from langflow.services.tracing.schema import Log


def get_distributed_trace_headers(trace_id, span_id):
    """生成分布式追踪的 Header 字典。

    契约：返回包含 `opik_parent_span_id` 与 `opik_trace_id` 的字典。
    失败语义：不抛异常。
    """
    return {"opik_parent_span_id": span_id, "opik_trace_id": trace_id}


class OpikTracer(BaseTracer):
    flow_id: str

    def __init__(
        self,
        trace_name: str,
        trace_type: str,
        project_name: str,
        trace_id: UUID,
        user_id: str | None = None,
        session_id: str | None = None,
    ):
        """初始化 Opik tracer。

        契约：初始化失败时 `ready=False`。
        副作用：可能触发 Opik 鉴权检查。
        失败语义：SDK 导入失败或鉴权失败时禁用。
        """
        self._project_name = project_name
        self.trace_name = trace_name
        self.trace_type = trace_type
        self.opik_trace_id = None
        self.user_id = user_id
        self.session_id = session_id
        self.flow_id = trace_name.split(" - ")[-1]
        self.spans: dict = {}

        config = self._get_config()
        self._ready: bool = self._setup_opik(config, trace_id) if config else False
        self._distributed_headers = None

    @property
    def ready(self):
        """指示 tracer 是否可用。"""
        return self._ready

    def _setup_opik(self, config: dict, trace_id: UUID) -> bool:
        """初始化 Opik 客户端并创建 trace 元数据。"""
        try:
            from opik import Opik
            from opik.api_objects.trace import TraceData

            self._client = Opik(project_name=self._project_name, _show_misconfiguration_message=False, **config)

            missing_configuration, _ = self._client._config.get_misconfiguration_detection_results()

            if missing_configuration:
                return False

            if not self._check_opik_auth(self._client):
                return False

            # Langflow Trace ID seems to always be random
            metadata = {
                "langflow_trace_id": trace_id,
                "langflow_trace_name": self.trace_name,
                "user_id": self.user_id,
                "created_from": "langflow",
            }
            self.trace = TraceData(
                name=self.flow_id,
                metadata=metadata,
                thread_id=self.session_id,
            )
            self.opik_trace_id = self.trace.id
        except ImportError:
            logger.exception("Could not import opik. Please install it with `pip install opik`.")
            return False

        except Exception as e:  # noqa: BLE001
            logger.exception(f"Error setting up opik tracer: {e}")
            return False

        return True

    def _check_opik_auth(self, opik_client) -> bool:
        """执行 Opik 鉴权检查。"""
        try:
            opik_client.auth_check()
        except Exception as e:  # noqa: BLE001
            logger.error(f"Opik auth check failed, OpikTracer will be disabled: {e}")
            return False
        else:
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
        """创建组件级 span 并缓存分布式头。"""
        if not self._ready:
            return

        from opik.api_objects.span import SpanData

        name = trace_name.removesuffix(f" ({trace_id})")
        processed_inputs = self._convert_to_opik_types(inputs) if inputs else {}
        processed_metadata = self._convert_to_opik_types(metadata) if metadata else {}

        span = SpanData(
            trace_id=self.opik_trace_id,
            name=name,
            input=processed_inputs,
            metadata=processed_metadata,
            type="general",  # The LLM span will comes from the langchain callback
        )

        self.spans[trace_id] = span
        self._distributed_headers = get_distributed_trace_headers(self.opik_trace_id, span.id)

    @override
    def end_trace(
        self,
        trace_id: str,
        trace_name: str,
        outputs: dict[str, Any] | None = None,
        error: Exception | None = None,
        logs: Sequence[Log | dict] = (),
    ) -> None:
        """结束组件级 span 并上报输出/错误。"""
        if not self._ready:
            return

        from opik.decorator.error_info_collector import collect

        span = self.spans.get(trace_id, None)

        if span:
            output: dict = {}
            output |= outputs or {}
            output |= {"logs": list(logs)} if logs else {}
            content = {"output": output, "error_info": collect(error) if error else None}

            span.init_end_time().update(**content)

            self._client.span(**span.__dict__)
        else:
            logger.warning("No corresponding span found")

    @override
    def end(
        self,
        inputs: dict[str, Any],
        outputs: dict[str, Any],
        error: Exception | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """结束根 trace 并 flush。"""
        if not self._ready:
            return

        from opik.decorator.error_info_collector import collect

        self.trace.init_end_time().update(
            input=inputs, output=outputs, error_info=collect(error) if error else None, metadata=metadata
        )

        self._client.trace(**self.trace.__dict__)

        self._client.flush()

    def get_langchain_callback(self) -> BaseCallbackHandler | None:
        """返回 LangChain Opik 回调处理器。"""
        if not self._ready:
            return None

        from opik.integrations.langchain import OpikTracer as LangchainOpikTracer

        # Set project name for the langchain integration
        os.environ["OPIK_PROJECT_NAME"] = self._project_name

        return LangchainOpikTracer(distributed_headers=self._distributed_headers)

    def _convert_to_opik_types(self, io_dict: dict[str | Any, Any]) -> dict[str, Any]:
        """将输入字典转换为 Opik 兼容格式。"""
        return {str(key): self._convert_to_opik_type(value) for key, value in io_dict.items() if key is not None}

    def _convert_to_opik_type(self, value):
        """递归转换为 Opik 兼容类型。"""
        if isinstance(value, dict):
            value = {key: self._convert_to_opik_type(val) for key, val in value.items()}

        elif isinstance(value, list):
            value = [self._convert_to_opik_type(v) for v in value]

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

        return value

    @staticmethod
    def _get_config() -> dict:
        """从环境变量读取 Opik 配置。"""
        host = os.getenv("OPIK_URL_OVERRIDE", None)
        api_key = os.getenv("OPIK_API_KEY", None)
        workspace = os.getenv("OPIK_WORKSPACE", None)

        # API Key is mandatory for Opik Cloud and URL is mandatory for Open-Source Opik Server
        if host or api_key:
            return {"host": host, "api_key": api_key, "workspace": workspace}
        return {}
