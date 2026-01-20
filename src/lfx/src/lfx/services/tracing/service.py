"""模块名称：轻量追踪服务实现

本模块提供 LFX 的轻量追踪服务实现，仅记录日志不接入外部平台。
使用场景：在不依赖外部追踪系统时提供最小可用追踪能力。
主要功能包括：
- 运行级追踪开始/结束
- 组件级追踪上下文
- 追踪日志与输出写入（日志化）
"""

# ruff: noqa: ARG002
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from lfx.log.logger import logger
from lfx.services.tracing.base import BaseTracingService

if TYPE_CHECKING:
    from uuid import UUID

    from langchain.callbacks.base import BaseCallbackHandler

    from lfx.custom.custom_component.component import Component


class TracingService(BaseTracingService):
    """轻量追踪服务实现（仅日志）。"""

    def __init__(self):
        """初始化追踪服务。"""
        super().__init__()
        self.deactivated = False
        self.set_ready()

    @property
    def name(self) -> str:
        """返回服务名称标识。"""
        return "tracing_service"

    async def start_tracers(
        self,
        run_id: UUID,
        run_name: str,
        user_id: str | None,
        session_id: str | None,
        project_name: str | None = None,
    ) -> None:
        """启动追踪（轻量实现，仅记录日志）。"""
        logger.debug(f"Trace started: {run_name}")

    async def end_tracers(self, outputs: dict, error: Exception | None = None) -> None:
        """结束追踪（轻量实现，仅记录日志）。"""
        logger.debug("Trace ended")

    @asynccontextmanager
    async def trace_component(
        self,
        component: Component,
        trace_name: str,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ):
        """组件级追踪上下文（轻量实现）。"""
        logger.debug(f"Tracing component: {trace_name}")
        yield self

    def add_log(self, trace_name: str, log: Any) -> None:
        """写入追踪日志（轻量实现）。"""
        logger.debug(f"Trace log: {trace_name}")

    def set_outputs(
        self,
        trace_name: str,
        outputs: dict[str, Any],
        output_metadata: dict[str, Any] | None = None,
    ) -> None:
        """设置追踪输出（轻量实现）。"""
        logger.debug(f"Trace outputs set: {trace_name}")

    def get_langchain_callbacks(self) -> list[BaseCallbackHandler]:
        """返回 LangChain 回调列表（轻量实现为空）。"""
        return []

    @property
    def project_name(self) -> str | None:
        """返回项目名称（轻量实现固定为 None）。"""
        return None

    async def teardown(self) -> None:
        """释放追踪服务资源。"""
        logger.debug("Tracing service teardown")
