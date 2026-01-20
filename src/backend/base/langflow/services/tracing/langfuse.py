"""
模块名称：Langfuse Tracer 适配

本模块实现 Langfuse 的 tracing 适配，用于记录组件级与流程级 span。
主要功能包括：
- 初始化 Langfuse 客户端并创建 trace
- 记录组件级 span 的输入/输出/日志
- 提供 LangChain 回调处理器

关键组件：
- `LangFuseTracer`

设计背景：为 Langflow 提供 Langfuse 可观测性接入。
注意事项：未配置 `LANGFUSE_*` 环境变量时会禁用。
"""

from __future__ import annotations

import os
from collections import OrderedDict
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from lfx.log.logger import logger
from typing_extensions import override

from langflow.serialization.serialization import serialize
from langflow.services.tracing.base import BaseTracer

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from langchain.callbacks.base import BaseCallbackHandler
    from lfx.graph.vertex.base import Vertex

    from langflow.services.tracing.schema import Log


class LangFuseTracer(BaseTracer):
    flow_id: str

    def __init__(
        self,
        trace_name: str,
        trace_type: str,
        project_name: str,
        trace_id: UUID,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        """初始化 Langfuse tracer。

        契约：初始化失败时 `ready=False`。
        副作用：可能进行 Langfuse 健康检查。
        失败语义：SDK 不可用或鉴权失败时禁用。
        """
        self.project_name = project_name
        self.trace_name = trace_name
        self.trace_type = trace_type
        self.trace_id = trace_id
        self.user_id = user_id
        self.session_id = session_id
        self.flow_id = trace_name.split(" - ")[-1]
        self.spans: dict = OrderedDict()  # spans that are not ended

        config = self._get_config()
        self._ready: bool = self.setup_langfuse(config) if config else False

    @property
    def ready(self):
        """指示 tracer 是否可用。"""
        return self._ready

    def setup_langfuse(self, config) -> bool:
        """配置并初始化 Langfuse 客户端。

        契约：配置可用时返回 `True`。
        副作用：执行 Langfuse health check。
        失败语义：SDK 导入失败或健康检查失败返回 `False`。
        """
        try:
            from langfuse import Langfuse

            self._client = Langfuse(**config)
            try:
                from langfuse.api.core.request_options import RequestOptions

                self._client.client.health.health(request_options=RequestOptions(timeout_in_seconds=1))
            except Exception as e:  # noqa: BLE001
                logger.debug(f"can not connect to Langfuse: {e}")
                return False
            self.trace = self._client.trace(
                id=str(self.trace_id),
                name=self.flow_id,
                user_id=self.user_id,
                session_id=self.session_id,
            )

        except ImportError:
            logger.exception("Could not import langfuse. Please install it with `pip install langfuse`.")
            return False

        except Exception as e:  # noqa: BLE001
            logger.debug(f"Error setting up LangSmith tracer: {e}")
            return False

        return True

    @override
    def add_trace(
        self,
        trace_id: str,  # actualy component id
        trace_name: str,
        trace_type: str,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        vertex: Vertex | None = None,
    ) -> None:
        """创建组件级 span 并记录输入与元数据。

        契约：`trace_id` 对应组件唯一标识。
        副作用：在 Langfuse 侧创建 span。
        失败语义：未就绪时静默返回。
        """
        start_time = datetime.now(tz=timezone.utc)
        if not self._ready:
            return

        metadata_: dict = {"from_langflow_component": True, "component_id": trace_id}
        metadata_ |= {"trace_type": trace_type} if trace_type else {}
        metadata_ |= metadata or {}

        name = trace_name.removesuffix(f" ({trace_id})")
        content_span = {
            "name": name,
            "input": inputs,
            "metadata": metadata_,
            "start_time": start_time,
        }

        # 注意：并发构建组件时父子关系不稳定，当前直接平铺
        span = self.trace.span(**serialize(content_span))

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
        """结束组件级 span 并写入输出/日志/错误。

        契约：仅对已存在的 span 生效。
        副作用：更新 span 输出并结束。
        失败语义：未就绪时静默返回。
        """
        end_time = datetime.now(tz=timezone.utc)
        if not self._ready:
            return

        span = self.spans.pop(trace_id, None)
        if span:
            output: dict = {}
            output |= outputs or {}
            output |= {"error": str(error)} if error else {}
            output |= {"logs": list(logs)} if logs else {}
            content = serialize({"output": output, "end_time": end_time})
            span.update(**content)

    @override
    def end(
        self,
        inputs: dict[str, Any],
        outputs: dict[str, Any],
        error: Exception | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """结束根 trace 并写入最终输入/输出。

        契约：写入最终输入/输出与元数据。
        副作用：更新 Langfuse trace。
        失败语义：未就绪时静默返回。
        """
        if not self._ready:
            return
        content_update = {
            "input": inputs,
            "output": outputs,
            "metadata": metadata,
        }
        self.trace.update(**serialize(content_update))

    def get_langchain_callback(self) -> BaseCallbackHandler | None:
        """返回 LangChain 回调处理器（若可用）。

        契约：若当前存在 span 则返回其 handler。
        失败语义：未就绪时返回 `None`。
        """
        if not self._ready:
            return None

        # get callback from parent span
        stateful_client = self.spans[next(reversed(self.spans))] if len(self.spans) > 0 else self.trace
        return stateful_client.get_langchain_handler()

    @staticmethod
    def _get_config() -> dict:
        """从环境变量读取 Langfuse 配置。

        契约：缺少任一配置项则返回空字典。
        """
        secret_key = os.getenv("LANGFUSE_SECRET_KEY", None)
        public_key = os.getenv("LANGFUSE_PUBLIC_KEY", None)
        host = os.getenv("LANGFUSE_HOST", None)
        if secret_key and public_key and host:
            return {"secret_key": secret_key, "public_key": public_key, "host": host}
        return {}
