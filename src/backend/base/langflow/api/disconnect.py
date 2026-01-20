"""
模块名称：断连感知的流式响应

本模块提供带断开连接回调的 `StreamingResponse`，主要用于长连接推流场景。主要功能包括：
- 监听 `http.disconnect` 并触发回调
- 支持同步/协程回调的统一调用方式

关键组件：
- DisconnectHandlerStreamingResponse：断连回调版响应

设计背景：构建/日志等流式接口需要在客户端断开时清理任务与资源。
注意事项：仅处理 `http.disconnect`；回调异常由上层中间件/日志处理。
"""

import asyncio
import typing

from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from starlette.responses import ContentStream
from starlette.types import Receive

class DisconnectHandlerStreamingResponse(StreamingResponse):
    """支持断连回调的流式响应。

    契约：`on_disconnect` 可为同步或协程函数，断开时仅调用一次。
    副作用：持续读取 `receive` 通道直至断开事件。
    失败语义：回调异常不在此处捕获，交由上层处理与记录。
    """
    def __init__(
        self,
        content: ContentStream,
        status_code: int = 200,
        headers: typing.Mapping[str, str] | None = None,
        media_type: str | None = None,
        background: BackgroundTask | None = None,
        on_disconnect: typing.Callable | None = None,
    ):
        super().__init__(content, status_code, headers, media_type, background)
        self.on_disconnect = on_disconnect

    async def listen_for_disconnect(self, receive: Receive) -> None:
        """监听断开事件并触发回调。

        契约：读取 `receive` 直到收到 `http.disconnect`，然后调用 `on_disconnect`。
        副作用：消费 ASGI `receive` 通道。
        失败语义：回调异常不在此处捕获，交由上层处理。
        """
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                if self.on_disconnect:
                    coro = self.on_disconnect()
                    if asyncio.iscoroutine(coro):
                        await coro
                break
