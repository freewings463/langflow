""" 
模块名称：流式回调与 Socket.IO 转发

本模块将 LangChain 回调事件转为前端可消费的流式消息，并通过 Socket.IO 推送。
主要功能：
- 在 LLM 生成与工具调用阶段推送 `ChatResponse`
- 将格式化后的 Prompt 发送给前端
- 记录流式推送失败的异常
设计背景：前端需要实时展示推理过程与工具输入输出。
注意事项：推送失败只记录异常，不中断上游任务。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from langchain_core.agents import AgentAction, AgentFinish
from langchain_core.callbacks.base import AsyncCallbackHandler
from lfx.log.logger import logger
from lfx.utils.util import remove_ansi_escape_codes
from typing_extensions import override

from langflow.api.v1.schemas import ChatResponse, PromptResponse
from langflow.services.deps import get_chat_service


# 迁移上下文：参考 https://github.com/hwchase17/chat-langchain/blob/master/callback.py 的回调结构。
class AsyncStreamingLLMCallbackHandleSIO(AsyncCallbackHandler):
    """LLM 流式回调处理器，将事件转为 Socket.IO 消息。"""

    @property
    def ignore_chain(self) -> bool:
        """是否忽略链式回调。"""
        return False

    def __init__(self, session_id: str):
        self.chat_service = get_chat_service()
        self.client_id = session_id
        self.sid = session_id

    @override
    async def on_llm_new_token(self, token: str, **kwargs: Any) -> None:  # type: ignore[misc]
        """将新 token 以 `stream` 形式推送给前端。"""
        resp = ChatResponse(message=token, type="stream", intermediate_steps="")
        await self.socketio_service.emit_token(to=self.sid, data=resp.model_dump())

    @override
    async def on_tool_start(self, serialized: dict[str, Any], input_str: str, **kwargs: Any) -> Any:  # type: ignore[misc]
        """工具启动时推送输入摘要。"""
        resp = ChatResponse(
            message="",
            type="stream",
            intermediate_steps=f"Tool input: {input_str}",
        )
        await self.socketio_service.emit_token(to=self.sid, data=resp.model_dump())

    async def on_tool_end(self, output: str, **kwargs: Any) -> Any:
        """工具结束时拆分输出并按 token 形式推送。

        契约：
        - 输入：`output` 为工具输出文本
        - 副作用：多次调用 `emit_token` 进行流式推送
        - 失败语义：推送失败仅记录异常，不向上抛出

        关键路径（三步）：
        1) 生成首段 `observation_prefix` 消息
        2) 拆分剩余输出并组装响应列表
        3) 顺序发送以模拟 token 流
        """
        observation_prefix = kwargs.get("observation_prefix", "Tool output: ")
        split_output = output.split()
        first_word = split_output[0]
        rest_of_output = split_output[1:]
        # 实现：首段携带前缀，剩余词逐条发送以贴近流式体验。
        intermediate_steps = f"{observation_prefix}{first_word}"

        # 实现：构造首条响应与后续分词响应。
        resp = ChatResponse(
            message="",
            type="stream",
            intermediate_steps=intermediate_steps,
        )
        rest_of_resps = [
            ChatResponse(
                message="",
                type="stream",
                intermediate_steps=f"{word}",
            )
            for word in rest_of_output
        ]
        resps = [resp, *rest_of_resps]

        try:
            # 实现：逐条发送以模拟 token 流。
            for resp in resps:
                await self.socketio_service.emit_token(to=self.sid, data=resp.model_dump())
        except Exception:  # noqa: BLE001
            await logger.aexception("Error sending response")

    async def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """工具运行报错时触发（当前保留空实现）。"""

    @override
    async def on_text(  # type: ignore[misc]
        self, text: str, **kwargs: Any
    ) -> Any:
        """收到文本事件时，转发格式化后的 Prompt。"""
        # 注意：仅在包含 `Prompt after formatting` 时向前端发送最终 Prompt。
        if "Prompt after formatting" in text:
            text = text.replace("Prompt after formatting:\n", "")
            text = remove_ansi_escape_codes(text)
            resp = PromptResponse(
                prompt=text,
            )
            await self.socketio_service.emit_message(to=self.sid, data=resp.model_dump())

    @override
    async def on_agent_action(  # type: ignore[misc]
        self, action: AgentAction, **kwargs: Any
    ) -> None:
        """转发 Agent 行为日志到前端。"""
        log = f"Thought: {action.log}"
        # 注意：多行日志拆分为多条消息，便于前端逐行展示。
        if "\n" in log:
            logs = log.split("\n")
            for log in logs:
                resp = ChatResponse(message="", type="stream", intermediate_steps=log)
                await self.socketio_service.emit_token(to=self.sid, data=resp.model_dump())
        else:
            resp = ChatResponse(message="", type="stream", intermediate_steps=log)
            await self.socketio_service.emit_token(to=self.sid, data=resp.model_dump())

    @override
    async def on_agent_finish(  # type: ignore[misc]
        self, finish: AgentFinish, **kwargs: Any
    ) -> Any:
        """Agent 完成时推送最终日志。"""
        resp = ChatResponse(
            message="",
            type="stream",
            intermediate_steps=finish.log,
        )
        await self.socketio_service.emit_token(to=self.sid, data=resp.model_dump())
