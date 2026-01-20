"""模块名称：追踪服务抽象基类

本模块定义追踪服务的最小接口，供轻量实现与完整实现统一对齐。
使用场景：Graph 运行时调用追踪服务时的接口约束。
主要功能包括：
- 定义 tracer 生命周期接口
- 定义组件级追踪上下文
- 定义日志与输出写入接口
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from lfx.services.base import Service

if TYPE_CHECKING:
    from uuid import UUID

    from langchain.callbacks.base import BaseCallbackHandler

    from lfx.custom.custom_component.component import Component


class BaseTracingService(Service, ABC):
    """追踪服务抽象基类。"""

    @abstractmethod
    def __init__(self):
        """初始化追踪服务。"""
        super().__init__()

    @abstractmethod
    async def start_tracers(
        self,
        run_id: UUID,
        run_name: str,
        user_id: str | None,
        session_id: str | None,
        project_name: str | None = None,
    ) -> None:
        """启动一次运行的追踪器。"""

    @abstractmethod
    async def end_tracers(self, outputs: dict, error: Exception | None = None) -> None:
        """结束一次运行的追踪器。"""

    @abstractmethod
    @asynccontextmanager
    async def trace_component(
        self,
        component: Component,
        trace_name: str,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ):
        """组件级追踪上下文管理器。"""

    @abstractmethod
    def add_log(self, trace_name: str, log: Any) -> None:
        """向当前追踪写入日志。"""

    @abstractmethod
    def set_outputs(
        self,
        trace_name: str,
        outputs: dict[str, Any],
        output_metadata: dict[str, Any] | None = None,
    ) -> None:
        """为当前追踪设置输出数据。"""

    @abstractmethod
    def get_langchain_callbacks(self) -> list[BaseCallbackHandler]:
        """返回 LangChain 回调处理器列表。"""

    @property
    @abstractmethod
    def project_name(self) -> str | None:
        """返回当前项目名称。"""
