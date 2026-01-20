"""
模块名称：构建流程事件与执行调度

本模块提供 `Flow` 构建的事件生产与响应封装，主要用于 `API` 层启动构建、推送事件与取消任务。主要功能包括：
- 构建图并按拓扑顺序调度组件执行
- 通过队列与事件管理器输出 `NDJSON`/`SSE` 事件
- 上报组件/流程级遥测并处理错误

关键组件：
- generate_flow_events：核心构建与事件生产
- get_flow_events_response/create_flow_response：事件消费与响应封装
- cancel_flow_build：取消构建任务与清理

设计背景：构建过程为长耗时且需流式反馈，需与前端协议兼容并可取消。
注意事项：队列以 `None` 标记结束；取消时必须触发 `event_manager.on_end` 释放订阅端。
"""

import asyncio
import json
import time
import traceback
import uuid
from collections.abc import AsyncIterator

from fastapi import BackgroundTasks, HTTPException, Response
from lfx.graph.graph.base import Graph
from lfx.graph.utils import log_vertex_build
from lfx.log.logger import logger
from lfx.schema.schema import InputValueRequest
from sqlmodel import select

from langflow.api.disconnect import DisconnectHandlerStreamingResponse
from langflow.api.utils import (
    CurrentActiveUser,
    EventDeliveryType,
    build_graph_from_data,
    build_graph_from_db,
    format_elapsed_time,
    format_exception_message,
    get_top_level_vertices,
    parse_exception,
)
from langflow.api.v1.schemas import FlowDataRequest, ResultDataResponse, VertexBuildResponse
from langflow.events.event_manager import EventManager
from langflow.exceptions.component import ComponentBuildError
from langflow.schema.message import ErrorMessage
from langflow.schema.schema import OutputValue
from langflow.services.database.models.flow.model import Flow
from langflow.services.deps import get_chat_service, get_telemetry_service, session_scope
from langflow.services.job_queue.service import JobQueueNotFoundError, JobQueueService
from langflow.services.telemetry.schema import ComponentInputsPayload, ComponentPayload, PlaygroundPayload
def _log_component_input_telemetry(
    vertex,
    vertex_id: str,
    component_run_id: str,
    background_tasks: BackgroundTasks,
    telemetry_service,
) -> None:
    """记录组件输入遥测（如可用）。

    契约：仅当 `vertex.custom_component` 存在且 `get_telemetry_input_values()` 返回非空时才入队。
    副作用：向 `background_tasks` 追加一次 `log_package_component_inputs` 调用。
    失败语义：不抛出异常；后台任务失败由遥测服务自身处理与记录。
    """
    if hasattr(vertex, "custom_component") and vertex.custom_component:
        inputs_dict = vertex.custom_component.get_telemetry_input_values()
        if inputs_dict:
            background_tasks.add_task(
                telemetry_service.log_package_component_inputs,
                ComponentInputsPayload(
                    component_run_id=component_run_id,
                    component_id=vertex_id,
                    component_name=vertex_id.split("-")[0],
                    component_inputs=inputs_dict,
                ),
            )


async def start_flow_build(
    *,
    flow_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    inputs: InputValueRequest | None,
    data: FlowDataRequest | None,
    files: list[str] | None,
    stop_component_id: str | None,
    start_component_id: str | None,
    log_builds: bool,
    current_user: CurrentActiveUser,
    queue_service: JobQueueService,
    flow_name: str | None = None,
) -> str:
    """启动构建任务并返回 `job_id`。

    契约：
    - 输入：`flow_id`、用户与队列服务；可选 `inputs/data/files` 用于构建与缓存。
    - 输出：新建 `job_id` 字符串，任务已入队等待执行。
    - 副作用：创建事件队列并启动后台协程。
    关键路径（三步）：
    1) 创建队列与事件管理器。
    2) 生成构建协程 `generate_flow_events`。
    3) 启动任务并返回 `job_id`。
    失败语义：队列创建或启动失败时抛 `HTTPException(500)`；调用方应提示重试。
    """
    job_id = str(uuid.uuid4())
    try:
        _, event_manager = queue_service.create_queue(job_id)
        task_coro = generate_flow_events(
            flow_id=flow_id,
            background_tasks=background_tasks,
            event_manager=event_manager,
            inputs=inputs,
            data=data,
            files=files,
            stop_component_id=stop_component_id,
            start_component_id=start_component_id,
            log_builds=log_builds,
            current_user=current_user,
            flow_name=flow_name,
        )
        queue_service.start_job(job_id, task_coro)
    except Exception as e:
        await logger.aexception("Failed to create queue and start task")
        raise HTTPException(status_code=500, detail=str(e)) from e
    return job_id


async def get_flow_events_response(
    *,
    job_id: str,
    queue_service: JobQueueService,
    event_delivery: EventDeliveryType,
):
    """获取构建事件（流式或轮询）。

    契约：
    - 输入：`job_id` + `event_delivery` 模式。
    - 输出：流式响应或 NDJSON（`application/x-ndjson`）轮询响应。
    - 副作用：读取队列并可能触发 `event_manager.on_end`。
    关键路径（三步）：
    1) 获取队列与事件任务。
    2) 按模式消费队列（流式或轮询）。
    3) 组装响应并返回。
    失败语义：
    - 未找到任务：`HTTPException(404)`；
    - 轮询取消：`HTTPException(499)`；
    - 轮询超时：返回空 NDJSON（不抛错）；
    - 其他异常：`HTTPException(500)`。
    排障入口：日志关键字 `Job not found` / `Unexpected error processing flow events`。
    """
    try:
        main_queue, event_manager, event_task, _ = queue_service.get_queue_data(job_id)
        if event_delivery in (EventDeliveryType.STREAMING, EventDeliveryType.DIRECT):
            if event_task is None:
                await logger.aerror(f"No event task found for job {job_id}")
                raise HTTPException(status_code=404, detail="No event task found for job")
            return await create_flow_response(
                queue=main_queue,
                event_manager=event_manager,
                event_task=event_task,
            )

        try:
            events: list = []
            while not main_queue.empty():
                _, value, _ = await main_queue.get()
                if value is None:
                    if event_task is not None:
                        event_task.cancel()
                    event_manager.on_end(data={})
                    events.append(None)
                    break
                events.append(value.decode("utf-8"))

            if not events:
                _, value, _ = await main_queue.get()
                if value is None:
                    if event_task is not None:
                        event_task.cancel()
                    event_manager.on_end(data={})
                else:
                    events.append(value.decode("utf-8"))

            content = "\n".join([event for event in events if event is not None])
            return Response(content=content, media_type="application/x-ndjson")
        except asyncio.CancelledError as exc:
            await logger.ainfo(f"Event polling was cancelled for job {job_id}")
            raise HTTPException(status_code=499, detail="Event polling was cancelled") from exc
        except asyncio.TimeoutError:
            await logger.awarning(f"Timeout while waiting for events for job {job_id}")
            return Response(content="", media_type="application/x-ndjson")

    except JobQueueNotFoundError as exc:
        await logger.aerror(f"Job not found: {job_id}. Error: {exc!s}")
        raise HTTPException(status_code=404, detail=f"Job not found: {exc!s}") from exc
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        await logger.aexception(f"Unexpected error processing flow events for job {job_id}")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {exc!s}") from exc


async def create_flow_response(
    queue: asyncio.Queue,
    event_manager: EventManager,
    event_task: asyncio.Task,
) -> DisconnectHandlerStreamingResponse:
    """封装构建事件的流式响应。

    契约：
    - 输入：`queue` 元素为 `(event_id, payload, put_time)`，其中 `payload=None` 表示流结束。
    - 输出：`DisconnectHandlerStreamingResponse`，`media_type=application/x-ndjson`。
    - 副作用：客户端断开时取消 `event_task` 并触发 `event_manager.on_end`。
    失败语义：消费队列异常时中止流并记录日志 `Error consuming event`。
    """

    async def consume_and_yield() -> AsyncIterator[str]:
        while True:
            try:
                event_id, value, put_time = await queue.get()
                if value is None:
                    break
                get_time = time.time()
                yield value.decode("utf-8")
                await logger.adebug(f"Event {event_id} consumed in {get_time - put_time:.4f}s")
            except Exception as exc:  # noqa: BLE001
                await logger.aexception(f"Error consuming event: {exc}")
                break

    def on_disconnect() -> None:
        logger.debug("Client disconnected, closing tasks")
        event_task.cancel()
        event_manager.on_end(data={})

    return DisconnectHandlerStreamingResponse(
        consume_and_yield(),
        media_type="application/x-ndjson",
        on_disconnect=on_disconnect,
    )


async def generate_flow_events(
    *,
    flow_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    event_manager: EventManager,
    inputs: InputValueRequest | None,
    data: FlowDataRequest | None,
    files: list[str] | None,
    stop_component_id: str | None,
    start_component_id: str | None,
    log_builds: bool,
    current_user: CurrentActiveUser,
    flow_name: str | None = None,
) -> None:
    """生成构建事件并驱动图执行。

    契约：输入为构建参数与事件管理器；输出为 `None`（事件通过队列/回调发出）。
    副作用：读写缓存、数据库查询、写遥测、更新图状态并向队列写入终止标记。
    关键路径（三步）：
    1) 构建图并排序顶点，缓存图与上报流程遥测。
    2) 并发构建顶点，发送顶点结束事件与组件遥测。
    3) 完成或失败时发送结束/错误事件并清理 `trace`。
    异常流：组件构建失败抛 `HTTPException(500)`；流式参数错误抛 `HTTPException(400)`。
    性能瓶颈：顶点构建依赖外部 I/O；并发度受可运行顶点数量影响。
    排障入口：日志关键字 `Error checking build status` / `Error building Component` / `Error building vertices`。
    """
    chat_service = get_chat_service()
    telemetry_service = get_telemetry_service()
    if not inputs:
        inputs = InputValueRequest(session=str(flow_id))

    async def build_graph_and_get_order() -> tuple[list[str], list[str], Graph]:
        """构建图并返回首层与可运行顶点集合。

        契约：返回 `(first_layer, vertices_to_run, graph)`；失败抛 `HTTPException` 并记录遥测。
        副作用：创建新 DB 会话、写缓存、上报流程遥测。
        关键路径（三步）：
        1) 创建新会话并构建图。
        2) 排序顶点并登记运行中的顶点。
        3) 缓存图并记录流程遥测。
        """
        start_time = time.perf_counter()
        components_count = 0
        graph = None
        run_id = str(uuid.uuid4())
        try:
            flow_id_str = str(flow_id)
            async with session_scope() as fresh_session:
                graph = await create_graph(fresh_session, flow_id_str, flow_name)

            graph.set_run_id(run_id)
            first_layer = sort_vertices(graph)

            for vertex_id in first_layer:
                graph.run_manager.add_to_vertices_being_run(vertex_id)

            components_count = len(graph.vertices)
            vertices_to_run = list(graph.vertices_to_run.union(get_top_level_vertices(graph, graph.vertices_to_run)))

            await chat_service.set_cache(flow_id_str, graph)
            await log_telemetry(start_time, components_count, run_id=run_id, success=True)

        except Exception as exc:
            await log_telemetry(start_time, components_count, run_id=run_id, success=False, error_message=str(exc))

            if "stream or streaming set to True" in str(exc):
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            await logger.aexception("Error checking build status")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return first_layer, vertices_to_run, graph

    async def log_telemetry(
        start_time: float,
        components_count: int,
        *,
        run_id: str | None = None,
        success: bool,
        error_message: str | None = None,
    ):
        """上报流程级遥测（耗时、组件数、成功状态）。

        契约：无返回值；将参数映射为 `PlaygroundPayload` 并入队。
        副作用：向 `background_tasks` 追加遥测任务。
        失败语义：任务执行失败由遥测服务记录，不在此处抛出。
        """
        background_tasks.add_task(
            telemetry_service.log_package_playground,
            PlaygroundPayload(
                playground_seconds=int(time.perf_counter() - start_time),
                playground_component_count=components_count,
                playground_success=success,
                playground_error_message=str(error_message) if error_message else "",
                playground_run_id=run_id,
            ),
        )

    async def create_graph(fresh_session, flow_id_str: str, flow_name: str | None) -> Graph:
        """构建图实例（优先使用请求数据，其次回退数据库）。

        契约：返回 `Graph` 实例；需要 `fresh_session` 为有效 DB 会话。
        副作用：可能访问数据库并读取 Flow 名称。
        失败语义：构建失败时向上抛异常，由上层统一转换为 `HTTPException`。
        """
        if inputs is not None and getattr(inputs, "session", None) is not None:
            effective_session_id = inputs.session
        else:
            effective_session_id = flow_id_str

        if not data:
            return await build_graph_from_db(
                flow_id=flow_id,
                session=fresh_session,
                chat_service=chat_service,
                user_id=str(current_user.id),
                session_id=effective_session_id,
            )

        if not flow_name:
            result = await fresh_session.exec(select(Flow.name).where(Flow.id == flow_id))
            flow_name = result.first()

        return await build_graph_from_data(
            flow_id=flow_id_str,
            payload=data.model_dump(),
            user_id=str(current_user.id),
            flow_name=flow_name,
            session_id=effective_session_id,
        )

    def sort_vertices(graph: Graph) -> list[str]:
        """计算执行顺序，失败时回退默认排序。

        契约：返回排序后的顶点 ID 列表。
        失败语义：排序异常时记录日志并调用默认排序。
        """
        try:
            return graph.sort_vertices(stop_component_id, start_component_id)
        except Exception:  # noqa: BLE001
            logger.exception("Error sorting vertices")
            return graph.sort_vertices()

    async def _build_vertex(vertex_id: str, graph: Graph, event_manager: EventManager) -> VertexBuildResponse:
        """构建单个顶点并生成响应数据。

        契约：返回 `VertexBuildResponse`；失败抛 `HTTPException(500)` 并写组件遥测。
        副作用：写缓存、更新图状态、记录组件与输入遥测、可能结束 `trace`。
        关键路径（三步）：
        1) 调用 `graph.build_vertex` 获取结果与可运行节点。
        2) 组装响应/错误结构并更新耗时与状态。
        3) 写入遥测与日志，返回 `VertexBuildResponse`。
        """
        flow_id_str = str(flow_id)
        next_runnable_vertices = []
        top_level_vertices = []
        start_time = time.perf_counter()
        error_message = None

        try:
            vertex = graph.get_vertex(vertex_id)
            try:
                lock = chat_service.async_cache_locks[flow_id_str]
                vertex_build_result = await graph.build_vertex(
                    vertex_id=vertex_id,
                    user_id=str(current_user.id),
                    inputs_dict=inputs.model_dump() if inputs else {},
                    files=files,
                    get_cache=chat_service.get_cache,
                    set_cache=chat_service.set_cache,
                    event_manager=event_manager,
                )
                result_dict = vertex_build_result.result_dict
                params = vertex_build_result.params
                valid = vertex_build_result.valid
                artifacts = vertex_build_result.artifacts
                next_runnable_vertices = await graph.get_next_runnable_vertices(lock, vertex=vertex, cache=False)
                top_level_vertices = graph.get_top_level_vertices(next_runnable_vertices)

                result_data_response = ResultDataResponse.model_validate(result_dict, from_attributes=True)
            except Exception as exc:  # noqa: BLE001
                if isinstance(exc, ComponentBuildError):
                    params = exc.message
                    tb = exc.formatted_traceback
                else:
                    tb = traceback.format_exc()
                    await logger.aexception("Error building Component")
                    params = format_exception_message(exc)
                message = {"errorMessage": params, "stackTrace": tb}
                valid = False
                error_message = params
                output_label = vertex.outputs[0]["name"] if vertex.outputs else "output"
                outputs = {output_label: OutputValue(message=message, type="error")}
                result_data_response = ResultDataResponse(results={}, outputs=outputs)
                artifacts = {}
                background_tasks.add_task(graph.end_all_traces_in_context(error=exc))

            result_data_response.message = artifacts

            if not vertex.will_stream and log_builds:
                background_tasks.add_task(
                    log_vertex_build,
                    flow_id=flow_id_str,
                    vertex_id=vertex_id,
                    valid=valid,
                    params=params,
                    data=result_data_response,
                    artifacts=artifacts,
                )
            else:
                await chat_service.set_cache(flow_id_str, graph)

            timedelta = time.perf_counter() - start_time

            if inputs and inputs.client_request_time:
                client_start_seconds = inputs.client_request_time / 1000
                current_time_seconds = time.time()
                timedelta = current_time_seconds - client_start_seconds

            duration = format_elapsed_time(timedelta)
            result_data_response.duration = duration
            result_data_response.timedelta = timedelta
            vertex.add_build_time(timedelta)
            inactivated_vertices = list(graph.inactivated_vertices.union(graph.conditionally_excluded_vertices))
            graph.reset_inactivated_vertices()
            graph.reset_activated_vertices()

            # 注意：`conditionally_excluded_vertices` 由 ConditionalRouter 管理，不能在此处重置。
            if graph.stop_vertex and graph.stop_vertex in next_runnable_vertices:
                # 注意：指定停止节点命中时，仅保留该节点，保证“立即停止”的语义。
                next_runnable_vertices = [graph.stop_vertex]

            if not graph.run_manager.vertices_being_run and not next_runnable_vertices:
                background_tasks.add_task(graph.end_all_traces_in_context())

            build_response = VertexBuildResponse(
                inactivated_vertices=list(set(inactivated_vertices)),
                next_vertices_ids=list(set(next_runnable_vertices)),
                top_level_vertices=list(set(top_level_vertices)),
                valid=valid,
                params=params,
                id=vertex.id,
                data=result_data_response,
            )

            _log_component_input_telemetry(vertex, vertex_id, graph.run_id, background_tasks, telemetry_service)

            background_tasks.add_task(
                telemetry_service.log_package_component,
                ComponentPayload(
                    component_name=vertex_id.split("-")[0],
                    component_id=vertex_id,
                    component_seconds=int(time.perf_counter() - start_time),
                    component_success=valid,
                    component_error_message=error_message,
                    component_run_id=graph.run_id,
                ),
            )
        except Exception as exc:
            if "vertex" in locals():
                _log_component_input_telemetry(vertex, vertex_id, graph.run_id, background_tasks, telemetry_service)

            background_tasks.add_task(
                telemetry_service.log_package_component,
                ComponentPayload(
                    component_name=vertex_id.split("-")[0],
                    component_id=vertex_id,
                    component_seconds=int(time.perf_counter() - start_time),
                    component_success=False,
                    component_error_message=str(exc),
                    component_run_id=graph.run_id,
                ),
            )
            await logger.aexception("Error building Component")
            message = parse_exception(exc)
            raise HTTPException(status_code=500, detail=message) from exc

        return build_response

    async def build_vertices(
        vertex_id: str,
        graph: Graph,
        event_manager: EventManager,
    ) -> None:
        """递归构建顶点并推送事件。

        契约：成功时触发 `event_manager.on_end_vertex`，并在可运行时继续调度下游顶点。
        关键路径（三步）：
        1) 构建当前顶点并序列化响应。
        2) 推送顶点结束事件。
        3) 并发调度下游可运行顶点。
        失败语义：序列化失败抛 `ValueError`；取消时抛 `CancelledError` 由上层处理。
        """
        try:
            vertex_build_response: VertexBuildResponse = await _build_vertex(vertex_id, graph, event_manager)
        except asyncio.CancelledError as exc:
            await logger.ainfo(f"Build cancelled: {exc}")
            raise

        try:
            vertex_build_response_json = vertex_build_response.model_dump_json()
            build_data = json.loads(vertex_build_response_json)
        except Exception as exc:
            msg = f"Error serializing vertex build response: {exc}"
            raise ValueError(msg) from exc

        event_manager.on_end_vertex(data={"build_data": build_data})

        if vertex_build_response.valid and vertex_build_response.next_vertices_ids:
            tasks = []
            for next_vertex_id in vertex_build_response.next_vertices_ids:
                task = asyncio.create_task(
                    build_vertices(
                        next_vertex_id,
                        graph,
                        event_manager,
                    )
                )
                tasks.append(task)
            await asyncio.gather(*tasks)

    try:
        ids, vertices_to_run, graph = await build_graph_and_get_order()
    except Exception as e:
        error_message = ErrorMessage(
            flow_id=flow_id,
            exception=e,
        )
        event_manager.on_error(data=error_message.data)
        raise

    event_manager.on_vertices_sorted(data={"ids": ids, "to_run": vertices_to_run})

    tasks = []
    for vertex_id in ids:
        task = asyncio.create_task(build_vertices(vertex_id, graph, event_manager))
        tasks.append(task)
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        background_tasks.add_task(graph.end_all_traces_in_context())
        raise
    except Exception as e:
        await logger.aerror(f"Error building vertices: {e}")
        custom_component = graph.get_vertex(vertex_id).custom_component
        trace_name = getattr(custom_component, "trace_name", None)
        error_message = ErrorMessage(
            flow_id=flow_id,
            exception=e,
            session_id=graph.session_id,
            trace_name=trace_name,
        )
        event_manager.on_error(data=error_message.data)
        raise

    event_manager.on_end(data={})
    await graph.end_all_traces()
    await event_manager.queue.put((None, None, time.time()))


async def cancel_flow_build(
    *,
    job_id: str,
    queue_service: JobQueueService,
) -> bool:
    """取消构建任务并验证取消状态。

    契约：返回 `True` 表示已取消或无需取消；返回 `False` 表示任务仍在运行。
    副作用：调用队列清理，触发任务取消。
    关键路径（三步）：
    1) 读取事件任务并检查是否已完成。
    2) 调用 `cleanup_job` 触发取消。
    3) 校验取消结果并返回。
    失败语义：`cleanup_job` 抛 `CancelledError` 时会根据任务状态决定是否继续抛出。
    排障入口：日志关键字 `Failed to cancel flow build` / `Successfully cancelled flow build`。
    """
    _, _, event_task, _ = queue_service.get_queue_data(job_id)

    if event_task is None:
        await logger.awarning(f"No event task found for job_id {job_id}")
        return True

    if event_task.done():
        await logger.ainfo(f"Task for job_id {job_id} is already completed")
        return True

    task_before_cleanup = event_task

    try:
        await queue_service.cleanup_job(job_id)
    except asyncio.CancelledError:
        if task_before_cleanup.cancelled():
            await logger.ainfo(f"Successfully cancelled flow build for job_id {job_id} (CancelledError caught)")
            return True
        await logger.aerror(f"CancelledError caught but task for job_id {job_id} was not cancelled")
        raise

    if task_before_cleanup.cancelled():
        await logger.ainfo(f"Successfully cancelled flow build for job_id {job_id}")
        return True

    await logger.aerror(f"Failed to cancel flow build for job_id {job_id}, task is still running")
    return False
