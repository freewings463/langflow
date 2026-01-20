"""
模块名称：多 `flow` 服务 API 工厂

本模块提供多 `flow` 的 FastAPI 应用工厂，主要用于将文件夹内的多个 `*.json` flow 统一暴露为 API。主要功能包括：
- 生成 `/flows/{flow_id}/run` 与 `/flows/{flow_id}/info` 等路由
- 提供 `/flows` 全局列表与健康检查
- 支持执行与流式输出的结果返回

关键组件：
- `create_multi_serve_app`：应用工厂入口
- `verify_api_key`：请求鉴权
- `consume_and_yield`：流式事件消费器

设计背景：CLI 需要一次性托管多个 flow 并提供统一的发现与调用入口。
注意事项：所有执行相关路由均要求 `x-api-key`（Header 或 Query），未配置将返回 401。
"""

from __future__ import annotations

import asyncio
import time
from copy import deepcopy
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Security
from fastapi.responses import StreamingResponse
from fastapi.security import APIKeyHeader, APIKeyQuery
from pydantic import BaseModel, Field

from lfx.cli.common import execute_graph_with_capture, extract_result_data, get_api_key
from lfx.log.logger import logger

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable
    from pathlib import Path

    from lfx.graph import Graph

# 注意：鉴权方式与 Langflow 主 API 保持一致
API_KEY_NAME = "x-api-key"
api_key_query = APIKeyQuery(name=API_KEY_NAME, scheme_name="API key query", auto_error=False)
api_key_header = APIKeyHeader(name=API_KEY_NAME, scheme_name="API key header", auto_error=False)


def verify_api_key(
    query_param: Annotated[str | None, Security(api_key_query)],
    header_param: Annotated[str | None, Security(api_key_header)],
) -> str:
    """校验 API Key。

    契约：从 query/header 读取 `x-api-key` 并与环境变量匹配。
    失败语义：缺失或不匹配时抛 `HTTPException(401)`；配置缺失抛 `HTTPException(500)`。
    副作用：读取环境变量。
    """
    provided_key = query_param or header_param
    if not provided_key:
        raise HTTPException(status_code=401, detail="API key required")

    try:
        expected_key = get_api_key()
        if provided_key != expected_key:
            raise HTTPException(status_code=401, detail="Invalid API key")
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    return provided_key


def _analyze_graph_structure(graph: Graph) -> dict[str, Any]:
    """分析图结构以生成动态文档信息。

    契约：返回包含组件列表、输入/输出类型、节点/边数量的字典。
    失败语义：结构解析异常时返回最小化的默认信息。
    副作用：无。

    关键路径（三步）：
    1) 遍历节点与边，收集组件与入/出度信息
    2) 基于模板字段推断输入/输出类型
    3) 将集合转为列表以便序列化
    """
    analysis: dict[str, Any] = {
        "components": [],
        "input_types": set(),
        "output_types": set(),
        "node_count": 0,
        "edge_count": 0,
        "entry_points": [],
        "exit_points": [],
    }

    try:
        for node_id, node in graph.nodes.items():
            analysis["node_count"] += 1
            component_info = {
                "id": node_id,
                "type": node.data.get("type", "Unknown"),
                "name": node.data.get("display_name", node.data.get("type", "Unknown")),
                "description": node.data.get("description", ""),
                "template": node.data.get("template", {}),
            }
            analysis["components"].append(component_info)

            if not any(edge.source == node_id for edge in graph.edges):
                analysis["entry_points"].append(component_info)

            if not any(edge.target == node_id for edge in graph.edges):
                analysis["exit_points"].append(component_info)

        analysis["edge_count"] = len(graph.edges)

        for entry in analysis["entry_points"]:
            template = entry.get("template", {})
            for field_config in template.values():
                if field_config.get("type") in ["str", "text", "string"]:
                    analysis["input_types"].add("text")
                elif field_config.get("type") in ["int", "float", "number"]:
                    analysis["input_types"].add("numeric")
                elif field_config.get("type") in ["file", "path"]:
                    analysis["input_types"].add("file")

        for exit_point in analysis["exit_points"]:
            template = exit_point.get("template", {})
            for field_config in template.values():
                if field_config.get("type") in ["str", "text", "string"]:
                    analysis["output_types"].add("text")
                elif field_config.get("type") in ["int", "float", "number"]:
                    analysis["output_types"].add("numeric")
                elif field_config.get("type") in ["file", "path"]:
                    analysis["output_types"].add("file")

    except (KeyError, AttributeError):
        # 注意：分析失败时返回最小化信息
        analysis["components"] = [{"type": "Unknown", "name": "Graph Component"}]
        analysis["input_types"] = {"text"}
        analysis["output_types"] = {"text"}

    # 注意：JSON 序列化不支持 set
    analysis["input_types"] = list(analysis["input_types"])
    analysis["output_types"] = list(analysis["output_types"])

    return analysis


def _generate_dynamic_run_description(graph: Graph) -> str:
    """生成 `/run` 端点的动态说明文案。

    契约：基于图结构分析生成输入/输出示例与统计信息。
    失败语义：结构缺失时使用默认示例。
    副作用：无。

    关键路径（三步）：
    1) 调用结构分析生成统计信息
    2) 构造输入/输出示例片段
    3) 拼装最终描述文本
    """
    analysis = _analyze_graph_structure(graph)

    input_examples = []
    for entry in analysis["entry_points"]:
        template = entry.get("template", {})
        for field_name, field_config in template.items():
            if field_config.get("type") in ["str", "text", "string"]:
                input_examples.append(f'"{field_name}": "Your input text here"')
            elif field_config.get("type") in ["int", "float", "number"]:
                input_examples.append(f'"{field_name}": 42')
            elif field_config.get("type") in ["file", "path"]:
                input_examples.append(f'"{field_name}": "/path/to/file.txt"')

    if not input_examples:
        input_examples = ['"input_value": "Your input text here"']

    output_examples = []
    for exit_point in analysis["exit_points"]:
        template = exit_point.get("template", {})
        for field_name, field_config in template.items():
            if field_config.get("type") in ["str", "text", "string"]:
                output_examples.append(f'"{field_name}": "Processed result"')
            elif field_config.get("type") in ["int", "float", "number"]:
                output_examples.append(f'"{field_name}": 123')
            elif field_config.get("type") in ["file", "path"]:
                output_examples.append(f'"{field_name}": "/path/to/output.txt"')

    if not output_examples:
        output_examples = ['"result": "Processed result"']

    description_parts = [
        f"Execute the deployed LFX graph with {analysis['node_count']} components.",
        "",
        "**Graph Analysis**:",
        f"- Entry points: {len(analysis['entry_points'])}",
        f"- Exit points: {len(analysis['exit_points'])}",
        f"- Input types: {', '.join(analysis['input_types']) if analysis['input_types'] else 'text'}",
        f"- Output types: {', '.join(analysis['output_types']) if analysis['output_types'] else 'text'}",
        "",
        "**Authentication Required**: Include your API key in the `x-api-key` header or as a query parameter.",
        "",
        "**Example Request**:",
        "```json",
        "{",
        f"  {', '.join(input_examples)}",
        "}",
        "```",
        "",
        "**Example Response**:",
        "```json",
        "{",
        f"  {', '.join(output_examples)},",
        '  "success": true,',
        '  "logs": "Graph execution completed successfully",',
        '  "type": "message",',
        '  "component": "FinalComponent"',
        "}",
        "```",
    ]

    return "\n".join(description_parts)


class FlowMeta(BaseModel):
    """`flow` 元数据模型。

    契约：`id` 为 UUIDv5；`relative_path` 为相对路径；`title` 为展示名称。
    失败语义：字段缺失时由 Pydantic 抛校验错误。
    副作用：无。
    """

    id: str = Field(..., description="Deterministic flow identifier (UUIDv5)")
    relative_path: str = Field(..., description="Path of the flow JSON relative to the deployed folder")
    title: str = Field(..., description="Human-readable title (filename stem if unknown)")
    description: str | None = Field(None, description="Optional flow description")


class RunRequest(BaseModel):
    """执行请求模型。

    契约：必须提供 `input_value`。
    失败语义：字段缺失时由 Pydantic 抛校验错误。
    副作用：无。
    """

    input_value: str = Field(..., description="Input value passed to the flow")


class StreamRequest(BaseModel):
    """流式执行请求模型。

    契约：`input_value` 必填，其余字段用于控制输出与会话状态。
    失败语义：字段缺失或类型错误时由 Pydantic 抛校验错误。
    副作用：无。
    """

    input_value: str = Field(..., description="Input value passed to the flow")
    input_type: str = Field(default="chat", description="Type of input (chat, text)")
    output_type: str = Field(default="chat", description="Type of output (chat, text, debug, any)")
    output_component: str | None = Field(default=None, description="Specific output component to stream from")
    session_id: str | None = Field(default=None, description="Session ID for maintaining conversation state")
    tweaks: dict[str, Any] | None = Field(default=None, description="Optional tweaks to modify flow behavior")


class RunResponse(BaseModel):
    """执行响应模型。

    契约：`success` 指示执行是否成功，`result/logs` 提供输出与诊断信息。
    失败语义：无（响应模型）。
    副作用：无。
    """

    result: str = Field(..., description="The output result from the flow execution")
    success: bool = Field(..., description="Whether execution was successful")
    logs: str = Field("", description="Captured logs from execution")
    type: str = Field("message", description="Type of result")
    component: str = Field("", description="Component that generated the result")


class ErrorResponse(BaseModel):
    """错误响应模型。

    契约：`success` 恒为 False，`error` 为可读错误信息。
    失败语义：无。
    副作用：无。
    """

    error: str = Field(..., description="Error message")
    success: bool = Field(default=False, description="Always false for errors")


# -----------------------------------------------------------------------------
# 流式辅助函数
# -----------------------------------------------------------------------------


async def consume_and_yield(queue: asyncio.Queue, client_consumed_queue: asyncio.Queue) -> AsyncGenerator:
    """消费事件队列并向客户端流式输出。

    契约：队列元素为 `(event_id, value, put_time)`；收到 `value is None` 结束。
    失败语义：异常直接上抛，由上层 StreamingResponse 处理。
    副作用：读取队列并写入日志。
    """
    while True:
        event_id, value, put_time = await queue.get()
        if value is None:
            break
        get_time = time.time()
        yield value
        get_time_yield = time.time()
        client_consumed_queue.put_nowait(event_id)
        logger.debug(
            f"consumed event {event_id} "
            f"(time in queue, {get_time - put_time:.4f}, "
            f"client {get_time_yield - get_time:.4f})"
        )


async def run_flow_generator_for_serve(
    graph: Graph,
    input_request: StreamRequest,
    flow_id: str,
    event_manager,
    client_consumed_queue: asyncio.Queue,
) -> None:
    """异步执行 flow 并驱动事件流。

    契约：成功时发送 `end` 事件，失败时发送 `error` 事件，并以 None 事件结束。
    失败语义：内部异常会记录日志并通过事件返回，不抛出给上层。
    副作用：执行图、写日志、向事件队列写入数据。

    注意：此处使用 `execute_graph_with_capture` 的简化流程，未接入完整流式管线。

    关键路径（三步）：
    1) 执行图并获取结果与日志
    2) 通过事件管理器发送最终结果或错误
    3) 推送结束事件并通知客户端消费
    """
    try:
        results, logs = await execute_graph_with_capture(graph, input_request.input_value)
        result_data = extract_result_data(results, logs)

        event_manager.on_end(data={"result": result_data})
        await client_consumed_queue.get()
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error running flow {flow_id}: {e}")
        event_manager.on_error(data={"error": str(e)})
    finally:
        await event_manager.queue.put((None, None, time.time()))


# -----------------------------------------------------------------------------
# 应用工厂
# -----------------------------------------------------------------------------


def create_multi_serve_app(
    *,
    root_dir: Path,  # noqa: ARG001
    graphs: dict[str, Graph],
    metas: dict[str, FlowMeta],
    verbose_print: Callable[[str], None],  # noqa: ARG001
) -> FastAPI:
    """创建多 `flow` FastAPI 应用。

    契约：`graphs` 与 `metas` 必须包含相同的 flow_id 集合。
    失败语义：键不一致时抛 `ValueError`。
    副作用：构建并返回 FastAPI 应用对象。

    关键路径（三步）：
    1) 创建全局列表与健康检查端点
    2) 为每个 flow 构建专属路由
    3) 注册路由并返回应用
    """
    if set(graphs) != set(metas):  # pragma: no cover  # 注意：健壮性检查
        msg = "graphs and metas must contain the same keys"
        raise ValueError(msg)

    app = FastAPI(
        title=f"LFX Multi-Flow Server ({len(graphs)})",
        description=(
            "This server hosts multiple LFX graphs under the `/flows/{id}` prefix. "
            "Use `/flows` to list available IDs then POST your input to `/flows/{id}/run`."
        ),
        version="1.0.0",
    )

    # ------------------------------------------------------------------
    # 全局端点
    # ------------------------------------------------------------------

    @app.get("/flows", response_model=list[FlowMeta], tags=["info"], summary="List available flows")
    async def list_flows():
        """返回全部 flow 的元数据。"""
        return list(metas.values())

    @app.get("/health", tags=["info"], summary="Global health check")
    async def global_health():
        return {"status": "healthy", "flow_count": len(graphs)}

    # ------------------------------------------------------------------
    # `flow` 路由
    # ------------------------------------------------------------------

    def create_flow_router(flow_id: str, graph: Graph, meta: FlowMeta) -> APIRouter:
        """为单个 flow 创建路由。

        契约：每个 flow 使用独立 Router，避免闭包捕获问题。
        失败语义：无。
        副作用：创建路由并注册依赖。
        """
        analysis = _analyze_graph_structure(graph)
        run_description = _generate_dynamic_run_description(graph)

        router = APIRouter(
            prefix=f"/flows/{flow_id}",
            tags=[meta.title or flow_id],
            dependencies=[Depends(verify_api_key)],  # 注意：该 Router 下所有路由均需鉴权
        )

        @router.post(
            "/run",
            response_model=RunResponse,
            responses={500: {"model": ErrorResponse}},
            summary="Execute flow",
            description=run_description,
        )
        async def run_flow(
            request: RunRequest,
        ) -> RunResponse:
            """执行单个 flow 并返回结果。

            契约：返回 `RunResponse`，失败时 `success=False` 且包含错误信息。
            失败语义：捕获异常并以错误响应返回，不抛出给客户端。
            副作用：执行图并生成日志。

            关键路径（三步）：
            1) 深拷贝图并执行获取结果/日志
            2) 构造成功响应或错误响应
            3) 返回统一结构的 `RunResponse`
            """
            try:
                graph_copy = deepcopy(graph)
                results, logs = await execute_graph_with_capture(graph_copy, request.input_value)
                result_data = extract_result_data(results, logs)

                logger.debug(f"Flow {flow_id} execution completed: {len(results)} results, {len(logs)} log chars")
                logger.debug(f"Flow {flow_id} result data: {result_data}")

                if not result_data.get("success", True):
                    error_message = result_data.get("result", result_data.get("text", "No response generated"))

                    error_logs = logs
                    if not error_logs.strip():
                        error_logs = (
                            f"Flow execution completed but no valid result was produced.\nResult data: {result_data}"
                        )

                    return RunResponse(
                        result=error_message,
                        success=False,
                        logs=error_logs,
                        type="error",
                        component=result_data.get("component", ""),
                    )

                return RunResponse(
                    result=result_data.get("result", result_data.get("text", "")),
                    success=result_data.get("success", True),
                    logs=logs,
                    type=result_data.get("type", "message"),
                    component=result_data.get("component", ""),
                )
            except Exception as exc:  # noqa: BLE001
                import traceback

                error_traceback = traceback.format_exc()
                error_message = f"Flow execution failed: {exc!s}"

                logger.error(f"Error running flow {flow_id}: {exc}")
                logger.debug(f"Full traceback for flow {flow_id}:\n{error_traceback}")

                return RunResponse(
                    result=error_message,
                    success=False,
                    logs=f"ERROR: {error_message}\n\nFull traceback:\n{error_traceback}",
                    type="error",
                    component="",
                )

        @router.post(
            "/stream",
            response_model=None,
            summary="Stream flow execution",
            description=f"Stream the execution of {meta.title or flow_id} with real-time events and token streaming.",
        )
        async def stream_flow(
            request: StreamRequest,
        ) -> StreamingResponse:
            """流式执行并返回事件流。

            契约：返回 `text/event-stream`；客户端断开时取消任务。
            失败语义：初始化失败时返回错误事件流。
            副作用：创建异步任务与事件队列。

            关键路径（三步）：
            1) 初始化事件管理器与队列
            2) 启动执行任务并监听断开
            3) 返回 StreamingResponse
            """
            try:
                # 注意：延迟导入避免循环依赖
                from lfx.events.event_manager import create_stream_tokens_event_manager

                asyncio_queue: asyncio.Queue = asyncio.Queue()
                asyncio_queue_client_consumed: asyncio.Queue = asyncio.Queue()
                event_manager = create_stream_tokens_event_manager(queue=asyncio_queue)

                main_task = asyncio.create_task(
                    run_flow_generator_for_serve(
                        graph=graph,
                        input_request=request,
                        flow_id=flow_id,
                        event_manager=event_manager,
                        client_consumed_queue=asyncio_queue_client_consumed,
                    )
                )

                async def on_disconnect() -> None:
                    logger.debug(f"Client disconnected from flow {flow_id}, closing tasks")
                    main_task.cancel()

                return StreamingResponse(
                    consume_and_yield(asyncio_queue, asyncio_queue_client_consumed),
                    background=on_disconnect,
                    media_type="text/event-stream",
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(f"Error setting up streaming for flow {flow_id}: {exc}")
                error_message = f"Failed to start streaming: {exc!s}"

                async def error_stream():
                    yield f'data: {{"error": "{error_message}", "success": false}}\n\n'

                return StreamingResponse(
                    error_stream(),
                    media_type="text/event-stream",
                )

        @router.get("/info", summary="Flow metadata", response_model=FlowMeta)
        async def flow_info():
            """返回 flow 元数据与基础分析。"""
            # 注意：为便于前端展示，返回中附带分析信息
            return {
                **meta.model_dump(),
                "components": analysis["node_count"],
                "connections": analysis["edge_count"],
                "input_types": analysis["input_types"],
                "output_types": analysis["output_types"],
            }

        return router

    for flow_id, graph in graphs.items():
        meta = metas[flow_id]
        router = create_flow_router(flow_id, graph, meta)
        app.include_router(router)

    return app
