"""
模块名称：Tracing 服务主实现

本模块实现 tracing 服务的核心功能，协调多个 tracing 提供商。
主要功能包括：
- 管理多种 tracer 实例（LangSmith、LangWatch、LangFuse、Arize Phoenix、Opik、Traceloop）
- 提供组件级别的追踪上下文管理
- 协调追踪任务的异步处理

关键组件：
- `TracingService`
- `TraceContext`
- `ComponentTraceContext`

设计背景：统一管理各种 tracing 服务，提供一致的追踪接口。
注意事项：追踪服务可能因配置或环境因素被禁用。
"""

from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from lfx.log.logger import logger

from langflow.services.base import Service

if TYPE_CHECKING:
    from uuid import UUID

    from langchain.callbacks.base import BaseCallbackHandler
    from lfx.custom.custom_component.component import Component
    from lfx.graph.vertex.base import Vertex
    from lfx.services.settings.service import SettingsService

    from langflow.services.tracing.base import BaseTracer
    from langflow.services.tracing.schema import Log


# 决策：动态导入各 tracer 类
# 问题：避免循环导入和延迟加载
# 方案：通过函数返回对应 tracer 类
# 代价：轻微的运行时开销
# 重评：如需优化性能可考虑其他导入策略

def _get_langsmith_tracer():
    """获取 LangSmith tracer 类。

    契约：返回 `LangSmithTracer` 类对象。
    失败语义：无，此函数不执行实例化。
    """
    from langflow.services.tracing.langsmith import LangSmithTracer

    return LangSmithTracer


def _get_langwatch_tracer():
    """获取 LangWatch tracer 类。

    契约：返回 `LangWatchTracer` 类对象。
    失败语义：无，此函数不执行实例化。
    """
    from langflow.services.tracing.langwatch import LangWatchTracer

    return LangWatchTracer


def _get_langfuse_tracer():
    """获取 LangFuse tracer 类。

    契约：返回 `LangFuseTracer` 类对象。
    失败语义：无，此函数不执行实例化。
    """
    from langflow.services.tracing.langfuse import LangFuseTracer

    return LangFuseTracer


def _get_arize_phoenix_tracer():
    """获取 Arize Phoenix tracer 类。

    契约：返回 `ArizePhoenixTracer` 类对象。
    失败语义：无，此函数不执行实例化。
    """
    from langflow.services.tracing.arize_phoenix import ArizePhoenixTracer

    return ArizePhoenixTracer


def _get_opik_tracer():
    """获取 Opik tracer 类。

    契约：返回 `OpikTracer` 类对象。
    失败语义：无，此函数不执行实例化。
    """
    from langflow.services.tracing.opik import OpikTracer

    return OpikTracer


def _get_traceloop_tracer():
    """获取 Traceloop tracer 类。

    契约：返回 `TraceloopTracer` 类对象。
    失败语义：无，此函数不执行实例化。
    """
    from langflow.services.tracing.traceloop import TraceloopTracer

    return TraceloopTracer


trace_context_var: ContextVar[TraceContext | None] = ContextVar("trace_context", default=None)
component_context_var: ContextVar[ComponentTraceContext | None] = ContextVar("component_trace_context", default=None)


class TraceContext:
    """追踪上下文容器。

    存储单次运行的追踪相关信息，包括追踪器实例、输入输出缓存及异步任务队列。
    """
    
    def __init__(
        self,
        run_id: UUID | None,
        run_name: str | None,
        project_name: str | None,
        user_id: str | None,
        session_id: str | None,
    ):
        """初始化追踪上下文。

        契约：设置运行 ID、名称、项目信息和用户/会话 ID。
        副作用：初始化输入/输出缓存和追踪队列。
        失败语义：不抛异常，所有参数可为空。
        """
        self.run_id: UUID | None = run_id
        self.run_name: str | None = run_name
        self.project_name: str | None = project_name
        self.user_id: str | None = user_id
        self.session_id: str | None = session_id
        self.tracers: dict[str, BaseTracer] = {}
        self.all_inputs: dict[str, dict] = defaultdict(dict)
        self.all_outputs: dict[str, dict] = defaultdict(dict)

        self.traces_queue: asyncio.Queue = asyncio.Queue()
        self.running = False
        self.worker_task: asyncio.Task | None = None


class ComponentTraceContext:
    """组件追踪上下文容器。

    存储单个组件的追踪相关信息，包括输入/输出、元数据和日志。
    """
    
    def __init__(
        self,
        trace_id: str,
        trace_name: str,
        trace_type: str,
        vertex: Vertex | None,
        inputs: dict[str, dict],
        metadata: dict[str, dict] | None = None,
    ):
        """初始化组件追踪上下文。

        契约：设置组件追踪的基本信息和数据缓存。
        副作用：初始化输出/日志缓存。
        失败语义：不抛异常，metadata 可为空。
        """
        self.trace_id: str = trace_id
        self.trace_name: str = trace_name
        self.trace_type: str = trace_type
        self.vertex: Vertex | None = vertex
        self.inputs: dict[str, dict] = inputs
        self.inputs_metadata: dict[str, dict] = metadata or {}
        self.outputs: dict[str, dict] = defaultdict(dict)
        self.outputs_metadata: dict[str, dict] = defaultdict(dict)
        self.logs: dict[str, list[Log | dict[Any, Any]]] = defaultdict(list)


class TracingService(Service):
    """Tracing 服务主实现。

    协调多种追踪提供商（如 LangSmith、LangWatch、LangFuse 等），为 Langflow 图运行提供统一的追踪功能。
    支持追踪整个图运行以及组件级别的子追踪。

    关键路径（三步）：
    1) start_tracers: 为图运行启动追踪
    2) trace_component: 为组件构建启动子追踪
    3) end_tracers: 结束图运行的追踪

    异常流：
    - 追踪服务可能由于配置不当或环境因素被禁用
    - 追踪过程中发生异常会被记录但不影响主流程
    - 队列处理异常会被捕获并记录

    性能瓶颈：
    - 异步队列处理可能会成为瓶颈
    - 大量并发组件追踪可能导致性能下降

    排障入口：
    - 追踪服务是否激活可通过 `deactivated` 属性检查
    - 日志关键字包括 `Error processing trace_func` 和 `Error starting tracing service`
    """

    name = "tracing_service"

    def __init__(self, settings_service: SettingsService):
        """初始化 TracingService。

        契约：通过 settings_service 获取追踪配置状态。
        副作用：根据配置决定是否激活追踪服务。
        失败语义：配置错误会导致服务被禁用。
        """
        self.settings_service = settings_service
        self.deactivated = self.settings_service.settings.deactivate_tracing

    async def _trace_worker(self, trace_context: TraceContext) -> None:
        """异步处理追踪队列中的任务。

        契约：持续处理追踪队列直到队列为空且追踪停止。
        副作用：执行队列中的追踪函数。
        失败语义：追踪函数异常会被记录但不会中断工作线程。
        """
        while trace_context.running or not trace_context.traces_queue.empty():
            trace_func, args = await trace_context.traces_queue.get()
            try:
                trace_func(*args)
            except Exception:  # noqa: BLE001
                await logger.aexception("Error processing trace_func")
            finally:
                trace_context.traces_queue.task_done()

    async def _start(self, trace_context: TraceContext) -> None:
        """启动追踪上下文的工作任务。

        契约：启动异步工作线程处理追踪任务。
        副作用：设置追踪上下文为运行状态并创建工作线程。
        失败语义：创建工作线程失败会记录错误。
        """
        if trace_context.running or self.deactivated:
            return
        try:
            trace_context.running = True
            trace_context.worker_task = asyncio.create_task(self._trace_worker(trace_context))
        except Exception:  # noqa: BLE001
            await logger.aexception("Error starting tracing service")

    def _initialize_langsmith_tracer(self, trace_context: TraceContext) -> None:
        """初始化 LangSmith 追踪器实例。

        契约：为指定的追踪上下文创建并存储 LangSmith 追踪器实例。
        副作用：将追踪器实例添加到追踪上下文的追踪器字典中。
        失败语义：初始化失败将导致该追踪器不可用。
        """
        langsmith_tracer = _get_langsmith_tracer()
        trace_context.tracers["langsmith"] = langsmith_tracer(
            trace_name=trace_context.run_name,
            trace_type="chain",
            project_name=trace_context.project_name,
            trace_id=trace_context.run_id,
        )

    def _initialize_langwatch_tracer(self, trace_context: TraceContext) -> None:
        """初始化 LangWatch 追踪器实例。

        契约：为指定的追踪上下文创建并存储 LangWatch 追踪器实例。
        副作用：将追踪器实例添加到追踪上下文的追踪器字典中。
        失败语义：服务被禁用或追踪器已存在且 ID 匹配时将跳过初始化。
        """
        if self.deactivated:
            return
        if (
            "langwatch" not in trace_context.tracers
            or trace_context.tracers["langwatch"].trace_id != trace_context.run_id
        ):
            langwatch_tracer = _get_langwatch_tracer()
            trace_context.tracers["langwatch"] = langwatch_tracer(
                trace_name=trace_context.run_name,
                trace_type="chain",
                project_name=trace_context.project_name,
                trace_id=trace_context.run_id,
            )

    def _initialize_langfuse_tracer(self, trace_context: TraceContext) -> None:
        """初始化 LangFuse 追踪器实例。

        契约：为指定的追踪上下文创建并存储 LangFuse 追踪器实例。
        副作用：将追踪器实例添加到追踪上下文的追踪器字典中。
        失败语义：服务被禁用时将跳过初始化。
        """
        if self.deactivated:
            return
        langfuse_tracer = _get_langfuse_tracer()
        trace_context.tracers["langfuse"] = langfuse_tracer(
            trace_name=trace_context.run_name,
            trace_type="chain",
            project_name=trace_context.project_name,
            trace_id=trace_context.run_id,
            user_id=trace_context.user_id,
            session_id=trace_context.session_id,
        )

    def _initialize_arize_phoenix_tracer(self, trace_context: TraceContext) -> None:
        """初始化 Arize Phoenix 追踪器实例。

        契约：为指定的追踪上下文创建并存储 Arize Phoenix 追踪器实例。
        副作用：将追踪器实例添加到追踪上下文的追踪器字典中。
        失败语义：服务被禁用时将跳过初始化。
        """
        if self.deactivated:
            return
        arize_phoenix_tracer = _get_arize_phoenix_tracer()
        trace_context.tracers["arize_phoenix"] = arize_phoenix_tracer(
            trace_name=trace_context.run_name,
            trace_type="chain",
            project_name=trace_context.project_name,
            trace_id=trace_context.run_id,
        )

    def _initialize_opik_tracer(self, trace_context: TraceContext) -> None:
        """初始化 Opik 追踪器实例。

        契约：为指定的追踪上下文创建并存储 Opik 追踪器实例。
        副作用：将追踪器实例添加到追踪上下文的追踪器字典中。
        失败语义：服务被禁用时将跳过初始化。
        """
        if self.deactivated:
            return
        opik_tracer = _get_opik_tracer()
        trace_context.tracers["opik"] = opik_tracer(
            trace_name=trace_context.run_name,
            trace_type="chain",
            project_name=trace_context.project_name,
            trace_id=trace_context.run_id,
            user_id=trace_context.user_id,
            session_id=trace_context.session_id,
        )

    def _initialize_traceloop_tracer(self, trace_context: TraceContext) -> None:
        """初始化 Traceloop 追踪器实例。

        契约：为指定的追踪上下文创建并存储 Traceloop 追踪器实例。
        副作用：将追踪器实例添加到追踪上下文的追踪器字典中。
        失败语义：服务被禁用时将跳过初始化。
        """
        if self.deactivated:
            return
        traceloop_tracer = _get_traceloop_tracer()
        trace_context.tracers["traceloop"] = traceloop_tracer(
            trace_name=trace_context.run_name,
            trace_type="chain",
            project_name=trace_context.project_name,
            trace_id=trace_context.run_id,
            user_id=trace_context.user_id,
            session_id=trace_context.session_id,
        )

    async def start_tracers(
        self,
        run_id: UUID,
        run_name: str,
        user_id: str | None,
        session_id: str | None,
        project_name: str | None = None,
    ) -> None:
        """启动图运行的追踪服务。

        契约：创建追踪上下文并初始化所有支持的追踪器。
        副作用：设置追踪上下文变量，启动异步工作线程。
        失败语义：服务被禁用时静默返回，初始化异常会被记录。
        """
        if self.deactivated:
            return
        try:
            project_name = project_name or os.getenv("LANGCHAIN_PROJECT", "Langflow")
            trace_context = TraceContext(run_id, run_name, project_name, user_id, session_id)
            trace_context_var.set(trace_context)
            await self._start(trace_context)
            self._initialize_langsmith_tracer(trace_context)
            self._initialize_langwatch_tracer(trace_context)
            self._initialize_langfuse_tracer(trace_context)
            self._initialize_arize_phoenix_tracer(trace_context)
            self._initialize_opik_tracer(trace_context)
            self._initialize_traceloop_tracer(trace_context)
        except Exception as e:  # noqa: BLE001
            await logger.adebug(f"Error initializing tracers: {e}")

    async def _stop(self, trace_context: TraceContext) -> None:
        """停止追踪上下文的工作任务。

        契约：停止追踪处理并等待队列任务完成。
        副作用：取消工作线程并清理资源。
        失败语义：停止过程中的异常会被记录但不会传播。
        """
        try:
            trace_context.running = False
            # check the qeue is empty
            if not trace_context.traces_queue.empty():
                await trace_context.traces_queue.join()
            if trace_context.worker_task:
                trace_context.worker_task.cancel()
                trace_context.worker_task = None

        except Exception:  # noqa: BLE001
            await logger.aexception("Error stopping tracing service")

    def _end_all_tracers(self, trace_context: TraceContext, outputs: dict, error: Exception | None = None) -> None:
        """结束所有追踪器的追踪。

        契约：为所有已准备就绪的追踪器调用结束方法。
        副作用：将最终输入、输出和错误信息传递给追踪器。
        失败语义：单个追踪器结束时的异常会被记录但不会传播。
        """
        for tracer in trace_context.tracers.values():
            if tracer.ready:
                try:
                    # why all_inputs and all_outputs? why metadata=outputs?
                    tracer.end(
                        trace_context.all_inputs,
                        outputs=trace_context.all_outputs,
                        error=error,
                        metadata=outputs,
                    )
                except Exception:  # noqa: BLE001
                    logger.error("Error ending all traces")

    async def end_tracers(self, outputs: dict, error: Exception | None = None) -> None:
        """结束图运行的追踪服务。

        契约：停止追踪上下文并结束所有追踪器。
        副作用：清理追踪上下文和工作线程。
        失败语义：服务被禁用或追踪上下文不存在时静默返回。
        """
        if self.deactivated:
            return
        trace_context = trace_context_var.get()
        if trace_context is None:
            return
        await self._stop(trace_context)
        self._end_all_tracers(trace_context, outputs, error)

    @staticmethod
    def _cleanup_inputs(inputs: dict[str, Any]):
        """清理输入数据，遮蔽敏感信息。

        契约：复制输入数据并遮蔽包含敏感关键词的字段。
        副作用：返回经过处理的输入副本，原数据不变。
        失败语义：不抛异常，始终返回处理后的数据。
        
        决策：使用静态方法处理输入清理
        问题：需要保护敏感信息不被记录
        方案：在输入阶段即刻遮蔽敏感字段
        代价：轻微的性能开销
        重评：如需更高性能可考虑其他脱敏策略
        """
        inputs = inputs.copy()
        sensitive_keywords = {"api_key", "password", "server_url"}

        def _mask(obj: Any):
            if isinstance(obj, dict):
                return {
                    k: "*****" if any(word in k.lower() for word in sensitive_keywords) else _mask(v)
                    for k, v in obj.items()
                }
            if isinstance(obj, list):
                return [_mask(i) for i in obj]
            return obj

        return _mask(inputs)

    def _start_component_traces(
        self,
        component_trace_context: ComponentTraceContext,
        trace_context: TraceContext,
    ) -> None:
        """启动组件级别的追踪。

        契约：为追踪上下文中的所有就绪追踪器添加组件追踪。
        副作用：清理输入数据并将其传递给追踪器。
        失败语义：单个追踪器添加追踪时的异常会被记录但不会中断其他追踪器。
        """
        inputs = self._cleanup_inputs(component_trace_context.inputs)
        component_trace_context.inputs = inputs
        component_trace_context.inputs_metadata = component_trace_context.inputs_metadata or {}
        for tracer in trace_context.tracers.values():
            if not tracer.ready:
                continue
            try:
                tracer.add_trace(
                    component_trace_context.trace_id,
                    component_trace_context.trace_name,
                    component_trace_context.trace_type,
                    inputs,
                    component_trace_context.inputs_metadata,
                    component_trace_context.vertex,
                )
            except Exception:  # noqa: BLE001
                logger.exception(f"Error starting trace {component_trace_context.trace_name}")

    def _end_component_traces(
        self,
        component_trace_context: ComponentTraceContext,
        trace_context: TraceContext,
        error: Exception | None = None,
    ) -> None:
        """结束组件级别的追踪。

        契约：为追踪上下文中的所有就绪追踪器结束组件追踪。
        副作用：将组件的输出和日志传递给追踪器。
        失败语义：单个追踪器结束追踪时的异常会被记录但不会中断其他追踪器。
        """
        for tracer in trace_context.tracers.values():
            if tracer.ready:
                try:
                    tracer.end_trace(
                        trace_id=component_trace_context.trace_id,
                        trace_name=component_trace_context.trace_name,
                        outputs=trace_context.all_outputs[component_trace_context.trace_name],
                        error=error,
                        logs=component_trace_context.logs[component_trace_context.trace_name],
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(f"Error ending trace {component_trace_context.trace_name}")

    @asynccontextmanager
    async def trace_component(
        self,
        component: Component,
        trace_name: str,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ):
        """追踪组件的执行过程。

        契约：创建组件追踪上下文并在组件执行前后启动和结束追踪。
        副作用：设置组件追踪上下文变量并将追踪任务加入队列。
        失败语义：服务被禁用或追踪上下文缺失时静默返回。

        决策：使用异步上下文管理器
        问题：需要在组件执行前后进行追踪记录
        方案：利用上下文管理器确保追踪开始和结束
        代价：引入异步上下文管理器的复杂性
        重评：如需简化可考虑其他追踪模式
        """
        if self.deactivated:
            yield self
            return
        trace_id = trace_name
        vertex = component.get_vertex()
        if vertex:
            trace_id = vertex.id
        trace_type = component.trace_type
        inputs = self._cleanup_inputs(inputs)
        component_trace_context = ComponentTraceContext(trace_id, trace_name, trace_type, vertex, inputs, metadata)
        component_context_var.set(component_trace_context)
        trace_context = trace_context_var.get()
        if trace_context is None:
            msg = "called trace_component but no trace context found"
            logger.warning(msg)
            yield self
            return
        trace_context.all_inputs[trace_name] |= inputs or {}
        await trace_context.traces_queue.put((self._start_component_traces, (component_trace_context, trace_context)))
        try:
            yield self
        except Exception as e:
            await trace_context.traces_queue.put(
                (self._end_component_traces, (component_trace_context, trace_context, e))
            )
            raise
        else:
            await trace_context.traces_queue.put(
                (self._end_component_traces, (component_trace_context, trace_context, None))
            )

    @property
    def project_name(self):
        """获取当前追踪的项目名称。

        契约：返回当前追踪上下文中的项目名称或默认值。
        副作用：无。
        失败语义：服务被禁用或追踪上下文缺失时返回默认值或 None。
        """
        if self.deactivated:
            return os.getenv("LANGCHAIN_PROJECT", "Langflow")
        trace_context = trace_context_var.get()
        if trace_context is None:
            msg = "called project_name but no trace context found"
            logger.warning(msg)
            return None
        return trace_context.project_name

    def add_log(self, trace_name: str, log: Log) -> None:
        """向当前组件追踪上下文添加日志。

        契约：将日志添加到指定追踪名称的日志列表中。
        副作用：修改组件追踪上下文中的日志列表。
        失败语义：服务被禁用时静默返回，组件上下文缺失时抛出 RuntimeError。
        """
        if self.deactivated:
            return
        component_context = component_context_var.get()
        if component_context is None:
            msg = "called add_log but no component context found"
            raise RuntimeError(msg)
        component_context.logs[trace_name].append(log)

    def set_outputs(
        self,
        trace_name: str,
        outputs: dict[str, Any],
        output_metadata: dict[str, Any] | None = None,
    ) -> None:
        """设置当前组件追踪上下文的输出。

        契约：将输出数据和元数据添加到组件追踪上下文和全局追踪上下文中。
        副作用：修改组件和全局追踪上下文中的输出字典。
        失败语义：服务被禁用时静默返回，上下文缺失时抛出相应异常。
        """
        if self.deactivated:
            return
        component_context = component_context_var.get()
        if component_context is None:
            msg = "called set_outputs but no component context found"
            raise RuntimeError(msg)
        component_context.outputs[trace_name] |= outputs or {}
        component_context.outputs_metadata[trace_name] |= output_metadata or {}
        trace_context = trace_context_var.get()
        if trace_context is None:
            msg = "called set_outputs but no trace context found"
            logger.warning(msg)
            return
        trace_context.all_outputs[trace_name] |= outputs or {}

    def get_tracer(self, tracer_name: str) -> BaseTracer | None:
        """获取指定名称的追踪器实例。

        契约：返回追踪上下文中指定名称的追踪器实例。
        副作用：无。
        失败语义：追踪上下文缺失时记录警告并返回 None。
        """
        trace_context = trace_context_var.get()
        if trace_context is None:
            msg = "called get_tracer but no trace context found"
            logger.warning(msg)
            return None
        return trace_context.tracers.get(tracer_name)

    def get_langchain_callbacks(self) -> list[BaseCallbackHandler]:
        """获取所有追踪器的 LangChain 回调处理器。

        契约：返回所有已就绪追踪器提供的 LangChain 回调处理器列表。
        副作用：无。
        失败语义：服务被禁用或追踪上下文缺失时返回空列表。
        """
        if self.deactivated:
            return []
        callbacks = []
        trace_context = trace_context_var.get()
        if trace_context is None:
            msg = "called get_langchain_callbacks but no trace context found"
            logger.warning(msg)
            return []
        for tracer in trace_context.tracers.values():
            if not tracer.ready:  # type: ignore[truthy-function]
                continue
            langchain_callback = tracer.get_langchain_callback()
            if langchain_callback:
                callbacks.append(langchain_callback)
        return callbacks
