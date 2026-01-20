"""
模块名称：日志查询与流式输出路由

本模块提供日志的批量查询与 `SSE` 流式输出接口，主要用于运维排障与调试。主要功能包括：
- 通过 `/logs` 按时间窗口获取日志片段
- 通过 `/logs-stream` 以 SSE 推送实时日志

关键组件：
- event_generator：从 `log_buffer` 生成 SSE 数据
- log_router：路由聚合器

设计背景：避免直接暴露日志文件，通过内存缓冲提供受控查询。
注意事项：接口必须鉴权；未启用日志缓冲时返回 501。
"""

import asyncio
import json
from http import HTTPStatus
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from lfx.log.logger import log_buffer

from langflow.services.auth.utils import get_current_active_user
log_router = APIRouter(tags=["Log"])


NUMBER_OF_NOT_SENT_BEFORE_KEEPALIVE = 5


async def event_generator(request: Request):
    """生成 `SSE` 日志流。

    契约：每条日志以 `json` 字符串输出，空闲时输出 `keepalive`。
    副作用：读取 `log_buffer` 并持有写锁进行快照。
    关键路径（三步）：
    1) 读取缓冲区快照并定位上次读取位置。
    2) 输出新增日志或 `keepalive`。
    3) 休眠 1 秒后继续轮询。
    失败语义：无显式异常；断开由 `request.is_disconnected()` 控制。
    """
    global log_buffer  # noqa: PLW0602
    last_read_item = None
    current_not_sent = 0
    while not await request.is_disconnected():
        to_write: list[Any] = []
        with log_buffer.get_write_lock():
            if last_read_item is None:
                last_read_item = log_buffer.buffer[len(log_buffer.buffer) - 1]
            else:
                found_last = False
                for item in log_buffer.buffer:
                    if found_last:
                        to_write.append(item)
                        last_read_item = item
                        continue
                    if item is last_read_item:
                        found_last = True
                        continue

                if not found_last:
                    for item in log_buffer.buffer:
                        to_write.append(item)
                        last_read_item = item
        if to_write:
            for ts, msg in to_write:
                yield f"{json.dumps({ts: msg})}\n\n"
        else:
            current_not_sent += 1
            if current_not_sent == NUMBER_OF_NOT_SENT_BEFORE_KEEPALIVE:
                current_not_sent = 0
                yield "keepalive\n\n"

        await asyncio.sleep(1)


@log_router.get("/logs-stream", dependencies=[Depends(get_current_active_user)])
async def stream_logs(
    request: Request,
):
    """实时日志流接口（`SSE`）。

    契约：需要鉴权；返回 `text/event-stream`。
    副作用：建立长连接并持续消费 `log_buffer`。
    关键路径（三步）：
    1) 校验日志缓冲是否启用。
    2) 绑定 `event_generator` 生成器。
    3) 返回流式响应。
    失败语义：日志缓冲未启用时返回 `HTTPException(501)`。
    安全：日志可能含敏感信息，必须鉴权。
    """
    global log_buffer  # noqa: PLW0602
    if log_buffer.enabled() is False:
        raise HTTPException(
            status_code=HTTPStatus.NOT_IMPLEMENTED,
            detail="Log retrieval is disabled",
        )

    return StreamingResponse(event_generator(request), media_type="text/event-stream")


@log_router.get("/logs", dependencies=[Depends(get_current_active_user)])
async def logs(
    lines_before: Annotated[int, Query(description="The number of logs before the timestamp or the last log")] = 0,
    lines_after: Annotated[int, Query(description="The number of logs after the timestamp")] = 0,
    timestamp: Annotated[int, Query(description="The timestamp to start getting logs from")] = 0,
):
    """按时间窗口获取日志片段。

    契约：`lines_before` 与 `lines_after` 互斥；`timestamp<=0` 默认取尾部日志。
    关键路径（三步）：
    1) 校验参数互斥关系与时间戳要求。
    2) 选择合适的缓冲读取策略。
    3) 返回 `JSONResponse`。
    失败语义：互斥条件冲突或缺失 `timestamp` 返回 `HTTPException(400)`；
    日志缓冲未启用返回 `HTTPException(501)`。
    安全：日志可能含敏感信息，必须鉴权。
    """
    global log_buffer  # noqa: PLW0602
    if log_buffer.enabled() is False:
        raise HTTPException(
            status_code=HTTPStatus.NOT_IMPLEMENTED,
            detail="Log retrieval is disabled",
        )
    if lines_after > 0 and lines_before > 0:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="Cannot request logs before and after the timestamp",
        )
    if timestamp <= 0:
        if lines_after > 0:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST,
                detail="Timestamp is required when requesting logs after the timestamp",
            )
        content = log_buffer.get_last_n(10) if lines_before <= 0 else log_buffer.get_last_n(lines_before)
    elif lines_before > 0:
        content = log_buffer.get_before_timestamp(timestamp=timestamp, lines=lines_before)
    elif lines_after > 0:
        content = log_buffer.get_after_timestamp(timestamp=timestamp, lines=lines_after)
    else:
        content = log_buffer.get_before_timestamp(timestamp=timestamp, lines=10)
    return JSONResponse(content=content)
