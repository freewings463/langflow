"""
模块名称：Tracing 抽象基类

本模块定义 tracing 接口契约，规范各厂商 tracer 实现。
主要功能包括：
- 定义 tracer 生命周期方法
- 统一 trace/span 的输入输出接口

关键组件：
- `BaseTracer`

设计背景：多厂商 tracing 需要统一的适配层。
注意事项：实现类必须遵循方法签名与行为约定。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from langchain.callbacks.base import BaseCallbackHandler
    from lfx.graph.vertex.base import Vertex

    from langflow.services.tracing.schema import Log


class BaseTracer(ABC):
    trace_id: UUID

    @abstractmethod
    def __init__(
        self,
        trace_name: str,
        trace_type: str,
        project_name: str,
        trace_id: UUID,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        """初始化 tracer 实例。

        契约：实现类应完成必要的 SDK 初始化与可用性判断。
        失败语义：初始化失败应保证 `ready=False`。
        """
        raise NotImplementedError

    @property
    @abstractmethod
    def ready(self) -> bool:
        """指示 tracer 是否可用。"""
        raise NotImplementedError

    @abstractmethod
    def add_trace(
        self,
        trace_id: str,
        trace_name: str,
        trace_type: str,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        vertex: Vertex | None = None,
    ) -> None:
        """创建组件级 trace/span。"""
        raise NotImplementedError

    @abstractmethod
    def end_trace(
        self,
        trace_id: str,
        trace_name: str,
        outputs: dict[str, Any] | None = None,
        error: Exception | None = None,
        logs: Sequence[Log | dict] = (),
    ) -> None:
        """结束组件级 trace/span。"""
        raise NotImplementedError

    @abstractmethod
    def end(
        self,
        inputs: dict[str, Any],
        outputs: dict[str, Any],
        error: Exception | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """结束根 trace 并写入最终输出。"""
        raise NotImplementedError

    @abstractmethod
    def get_langchain_callback(self) -> BaseCallbackHandler | None:
        """返回 LangChain 回调处理器（如有）。"""
        raise NotImplementedError
