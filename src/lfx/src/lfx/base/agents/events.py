"""
模块名称：代理事件流处理

本模块提供代理事件流的异步处理与消息拼装逻辑，主要用于将 `LangChain` 事件映射为
`LangFlow` 的 `Message`/`ContentBlock` 结构并在流式场景下保持一致性。
主要功能包括：
- 事件类型分发与处理
- 流式 token/工具调用的增量更新
- 运行耗时统计与异常语义封装

关键组件：
- `ExceptionWithMessageError`：携带消息的异常
- `process_agent_events`：事件处理总入口
- 各 `handle_*` 事件处理函数

设计背景：需要在流式与非流式场景下统一事件语义与消息结构。
注意事项：流式场景下需保持消息 ID 一致，避免重复落库。
"""

import asyncio
from collections.abc import AsyncIterator
from time import perf_counter
from typing import Any, Protocol

from langchain_core.agents import AgentFinish
from langchain_core.messages import AIMessageChunk, BaseMessage
from typing_extensions import TypedDict

from lfx.schema.content_block import ContentBlock
from lfx.schema.content_types import TextContent, ToolContent
from lfx.schema.log import OnTokenFunctionType, SendMessageFunctionType
from lfx.schema.message import Message


class ExceptionWithMessageError(Exception):
    """携带消息的异常类

    契约：
    - 输入：代理消息和错误消息
    - 输出：ExceptionWithMessageError 实例
    - 副作用：保存代理消息和错误消息
    - 失败语义：表示特定的错误情况
    """
    def __init__(self, agent_message: Message, message: str):
        """初始化异常实例

        契约：
        - 输入：代理消息和错误消息
        - 输出：ExceptionWithMessageError 实例
        - 副作用：保存代理消息和错误消息
        - 失败语义：无
        """
        self.agent_message = agent_message
        super().__init__(message)
        self.message = message

    def __str__(self):
        """返回异常的字符串表示

        契约：
        - 输入：无
        - 输出：包含代理消息和错误信息的字符串
        - 副作用：无
        - 失败语义：无
        """
        return (
            f"Agent message: {self.agent_message.text} \nError: {self.message}."
            if self.agent_message.error or self.agent_message.text
            else f"{self.message}."
        )


class InputDict(TypedDict):
    """输入字典类型定义

    契约：
    - 输入：无
    - 输出：TypedDict 类型
    - 副作用：定义输入字典的结构
    - 失败语义：无
    """
    input: str
    chat_history: list[BaseMessage]


def _build_agent_input_text_content(agent_input_dict: InputDict) -> str:
    """构建代理输入文本内容

    契约：
    - 输入：输入字典
    - 输出：输入文本字符串
    - 副作用：无
    - 失败语义：如果获取不到输入，默认返回空字符串
    """
    final_input = agent_input_dict.get("input", "")
    return f"{final_input}"


def _calculate_duration(start_time: float) -> int:
    """计算从开始时间到现在的时间间隔（毫秒）

    契约：
    - 输入：开始时间
    - 输出：以毫秒为单位的时间间隔
    - 副作用：无
    - 失败语义：如果计算失败，返回 0
    """
    # 计算耗时
    current_time = perf_counter()
    if isinstance(start_time, int):
        # 若为整数则按毫秒处理
        duration = current_time - (start_time / 1000)
        result = int(duration * 1000)
    else:
        # 若为浮点则按 `perf_counter` 秒处理
        result = int((current_time - start_time) * 1000)

    return result


async def handle_on_chain_start(
    event: dict[str, Any],
    agent_message: Message,
    send_message_callback: SendMessageFunctionType,
    send_token_callback: OnTokenFunctionType | None,  # noqa: ARG001
    start_time: float,
    *,
    had_streaming: bool = False,  # noqa: ARG001
    message_id: str | None = None,  # noqa: ARG001
) -> tuple[Message, float]:
    """处理链开始事件

    关键路径（三步）：
    1) 检查并创建内容块
    2) 提取输入数据并创建文本内容
    3) 发送消息并更新开始时间

    异常流：无。
    性能瓶颈：无显著性能瓶颈。
    排障入口：无。
    
    契约：
    - 输入：事件数据、代理消息、回调函数、开始时间等
    - 输出：更新后的消息和时间
    - 副作用：可能向消息添加内容块
    - 失败语义：如果处理失败，返回原始消息和时间
    """
    # 注意：如不存在内容块则创建
    if not agent_message.content_blocks:
        agent_message.content_blocks = [ContentBlock(title="Agent Steps", contents=[])]

    if event["data"].get("input"):
        input_data = event["data"].get("input")
        if isinstance(input_data, dict) and "input" in input_data:
            # 实现：将 `input_data` 转为 `InputDict`
            input_message = input_data.get("input", "")
            if isinstance(input_message, BaseMessage):
                input_message = input_message.text()
            elif not isinstance(input_message, str):
                input_message = str(input_message)

            input_dict: InputDict = {
                "input": input_message,
                "chat_history": input_data.get("chat_history", []),
            }
            text_content = TextContent(
                type="text",
                text=_build_agent_input_text_content(input_dict),
                duration=_calculate_duration(start_time),
                header={"title": "Input", "icon": "MessageSquare"},
            )
            agent_message.content_blocks[0].contents.append(text_content)
            agent_message = await send_message_callback(message=agent_message, skip_db_update=True)
            start_time = perf_counter()
    return agent_message, start_time


def _extract_output_text(output: str | list) -> str:
    """从输出中提取文本

    契约：
    - 输入：输出（字符串或列表）
    - 输出：提取的文本字符串
    - 副作用：无
    - 失败语义：如果提取失败，返回空字符串
    """
    if isinstance(output, str):
        return output
    if isinstance(output, list) and len(output) == 0:
        return ""

    # 处理不同长度与格式的列表
    if isinstance(output, list):
        # 处理单元素列表
        if len(output) == 1:
            item = output[0]
            if isinstance(item, str):
                return item
            if isinstance(item, dict):
                if "text" in item:
                    return item["text"] or ""
                if "content" in item:
                    return str(item["content"])
                if "message" in item:
                    return str(item["message"])

                # 特殊处理：非文本类字典
                if (
                    item.get("type") == "tool_use"  # 工具调用项
                    or ("index" in item and len(item) == 1)  # 仅索引项
                    or "partial_json" in item  # 部分 JSON 片段
                    # 仅索引项
                    or ("index" in item and not any(k in item for k in ("text", "content", "message")))
                    # 仅元数据且无有效文本
                    or not any(key in item for key in ["text", "content", "message"])
                ):
                    return ""

                # 其他字典格式返回空字符串
                return ""
            # 其他单元素类型（非 `str`/`dict`）返回空字符串
            return ""

        # 处理多元素列表：提取所有文本项
        text_parts = []
        for item in output:
            if isinstance(item, str):
                text_parts.append(item)
            elif isinstance(item, dict):
                if "text" in item and item["text"] is not None:
                    text_parts.append(item["text"])
                # 跳过 `tool_use`/仅索引/`partial_json` 项
                elif item.get("type") == "tool_use" or "partial_json" in item or ("index" in item and len(item) == 1):
                    continue
        return "".join(text_parts)

    # 兜底：格式异常时返回空字符串
    return ""


async def handle_on_chain_end(
    event: dict[str, Any],
    agent_message: Message,
    send_message_callback: SendMessageFunctionType,
    send_token_callback: OnTokenFunctionType | None,  # noqa: ARG001
    start_time: float,
    *,
    had_streaming: bool = False,
    message_id: str | None = None,  # noqa: ARG001
) -> tuple[Message, float]:
    """处理链结束事件

    关键路径（三步）：
    1) 提取输出文本
    2) 创建文本内容并添加到消息
    3) 发送最终消息并更新时间

    异常流：无。
    性能瓶颈：无显著性能瓶颈。
    排障入口：无。
    
    契约：
    - 输入：事件数据、代理消息、回调函数、开始时间等
    - 输出：更新后的消息和时间
    - 副作用：可能更新消息状态和内容
    - 失败语义：如果处理失败，返回原始消息和时间
    """
    data_output = event["data"].get("output")
    if data_output and isinstance(data_output, AgentFinish) and data_output.return_values.get("output"):
        output = data_output.return_values.get("output")

        agent_message.text = _extract_output_text(output)
        agent_message.properties.state = "complete"
        # 注意：若存在内容块则补充耗时
        if agent_message.content_blocks:
            duration = _calculate_duration(start_time)
            text_content = TextContent(
                type="text",
                text=agent_message.text,
                duration=duration,
                header={"title": "Output", "icon": "MessageSquare"},
            )
            agent_message.content_blocks[0].contents.append(text_content)

        # 注意：非流式才发送最终消息
        # 注意：流式场景前端已累积分片
        if not had_streaming:
            agent_message = await send_message_callback(message=agent_message)
        start_time = perf_counter()
    return agent_message, start_time


async def handle_on_tool_start(
    event: dict[str, Any],
    agent_message: Message,
    tool_blocks_map: dict[str, ToolContent],
    send_message_callback: SendMessageFunctionType,
    start_time: float,
) -> tuple[Message, float]:
    """处理工具开始事件

    关键路径（三步）：
    1) 提取工具相关信息
    2) 创建工具内容并添加到消息
    3) 发送消息并更新时间

    异常流：无。
    性能瓶颈：无显著性能瓶颈。
    排障入口：无。
    
    契约：
    - 输入：事件数据、代理消息、工具块映射、回调函数、开始时间
    - 输出：更新后的消息和时间
    - 副作用：向消息和映射添加工具内容
    - 失败语义：如果处理失败，返回原始消息和时间
    """
    tool_name = event["name"]
    tool_input = event["data"].get("input")
    run_id = event.get("run_id", "")
    tool_key = f"{tool_name}_{run_id}"

    # 注意：如不存在内容块则创建
    if not agent_message.content_blocks:
        agent_message.content_blocks = [ContentBlock(title="Agent Steps", contents=[])]

    duration = _calculate_duration(start_time)
    new_start_time = perf_counter()  # 注意：为下一步重置起始时间

    # 实现：按原始输入创建工具内容
    tool_content = ToolContent(
        type="tool_use",
        name=tool_name,
        tool_input=tool_input,
        output=None,
        error=None,
        header={"title": f"Accessing **{tool_name}**", "icon": "Hammer"},
        duration=duration,  # Store the actual duration
    )

    # 实现：写入映射并追加到消息
    tool_blocks_map[tool_key] = tool_content
    agent_message.content_blocks[0].contents.append(tool_content)

    agent_message = await send_message_callback(message=agent_message, skip_db_update=True)
    if agent_message.content_blocks and agent_message.content_blocks[0].contents:
        tool_blocks_map[tool_key] = agent_message.content_blocks[0].contents[-1]
    return agent_message, new_start_time


async def handle_on_tool_end(
    event: dict[str, Any],
    agent_message: Message,
    tool_blocks_map: dict[str, ToolContent],
    send_message_callback: SendMessageFunctionType,
    start_time: float,
) -> tuple[Message, float]:
    """处理工具结束事件

    关键路径（三步）：
    1) 从映射中获取工具内容
    2) 更新工具内容的执行结果
    3) 发送消息并更新时间

    异常流：无。
    性能瓶颈：无显著性能瓶颈。
    排障入口：无。
    
    契约：
    - 输入：事件数据、代理消息、工具块映射、回调函数、开始时间
    - 输出：更新后的消息和时间
    - 副作用：更新工具内容的输出和头信息
    - 失败语义：如果处理失败，返回原始消息和时间
    """
    run_id = event.get("run_id", "")
    tool_name = event.get("name", "")
    tool_key = f"{tool_name}_{run_id}"
    tool_content = tool_blocks_map.get(tool_key)

    if tool_content and isinstance(tool_content, ToolContent):
        # 注意：先刷新消息结构再定位工具内容
        agent_message = await send_message_callback(message=agent_message, skip_db_update=True)
        new_start_time = perf_counter()

        # 实现：在最新消息中定位并更新工具内容
        duration = _calculate_duration(start_time)
        tool_key = f"{tool_name}_{run_id}"

        # 实现：找到更新后的对应工具内容
        updated_tool_content = None
        if agent_message.content_blocks and agent_message.content_blocks[0].contents:
            for content in agent_message.content_blocks[0].contents:
                if (
                    isinstance(content, ToolContent)
                    and content.name == tool_name
                    and content.tool_input == tool_content.tool_input
                ):
                    updated_tool_content = content
                    break

        # 注意：只更新消息中实际存在的内容
        if updated_tool_content:
            updated_tool_content.duration = duration
            updated_tool_content.header = {"title": f"Executed **{updated_tool_content.name}**", "icon": "Hammer"}
            updated_tool_content.output = event["data"].get("output")

            # 实现：同步更新映射引用
            tool_blocks_map[tool_key] = updated_tool_content

        return agent_message, new_start_time
    return agent_message, start_time


async def handle_on_tool_error(
    event: dict[str, Any],
    agent_message: Message,
    tool_blocks_map: dict[str, ToolContent],
    send_message_callback: SendMessageFunctionType,
    start_time: float,
) -> tuple[Message, float]:
    """处理工具错误事件

    关键路径（三步）：
    1) 从映射中获取工具内容
    2) 更新工具内容的错误信息
    3) 发送消息并更新时间

    异常流：无。
    性能瓶颈：无显著性能瓶颈。
    排障入口：无。
    
    契约：
    - 输入：事件数据、代理消息、工具块映射、回调函数、开始时间
    - 输出：更新后的消息和时间
    - 副作用：更新工具内容的错误信息
    - 失败语义：如果处理失败，返回原始消息和时间
    """
    run_id = event.get("run_id", "")
    tool_name = event.get("name", "")
    tool_key = f"{tool_name}_{run_id}"
    tool_content = tool_blocks_map.get(tool_key)

    if tool_content and isinstance(tool_content, ToolContent):
        tool_content.error = event["data"].get("error", "Unknown error")
        tool_content.duration = _calculate_duration(start_time)
        tool_content.header = {"title": f"Error using **{tool_content.name}**", "icon": "Hammer"}
        agent_message = await send_message_callback(message=agent_message, skip_db_update=True)
        start_time = perf_counter()
    return agent_message, start_time


async def handle_on_chain_stream(
    event: dict[str, Any],
    agent_message: Message,
    send_message_callback: SendMessageFunctionType,  # noqa: ARG001
    send_token_callback: OnTokenFunctionType | None,
    start_time: float,
    *,
    had_streaming: bool = False,  # noqa: ARG001
    message_id: str | None = None,
) -> tuple[Message, float]:
    """处理链流事件

    关键路径（三步）：
    1) 检查是否有输出数据
    2) 如果有，更新消息文本
    3) 如果有回调，发送令牌事件

    异常流：无。
    性能瓶颈：无显著性能瓶颈。
    排障入口：无。
    
    契约：
    - 输入：事件数据、代理消息、回调函数、开始时间等
    - 输出：更新后的消息和时间
    - 副作用：可能更新消息文本和发送令牌事件
    - 失败语义：如果处理失败，返回原始消息和时间
    """
    data_chunk = event["data"].get("chunk", {})
    if isinstance(data_chunk, dict) and data_chunk.get("output"):
        output = data_chunk.get("output")
        if output and isinstance(output, str | list):
            agent_message.text = _extract_output_text(output)
        agent_message.properties.state = "complete"
        # 注意：此处不能发送回调，需原地更新以保持消息 `ID` 一致
        # 注意：最终消息在循环结束后统一发送
        start_time = perf_counter()
    elif isinstance(data_chunk, AIMessageChunk):
        output_text = _extract_output_text(data_chunk.content)
        # 注意：流式场景若回调存在则发送 `token` 事件
        # 注意：回调保持可选以兼容旧版本（`v1.6.5`）
        if output_text and output_text.strip() and send_token_callback and message_id:
            await asyncio.to_thread(
                send_token_callback,
                data={
                    "chunk": output_text,
                    "id": str(message_id),
                },
            )

        if not agent_message.text:
            # 注意：首条消息生成时启动计时
            start_time = perf_counter()
    return agent_message, start_time


class ToolEventHandler(Protocol):
    """工具事件处理器协议

    契约：
    - 输入：事件数据、代理消息、工具块映射、回调函数、开始时间
    - 输出：更新后的消息和时间
    - 副作用：处理工具事件
    - 失败语义：如果处理失败，返回原始消息和时间
    """
    async def __call__(
        self,
        event: dict[str, Any],
        agent_message: Message,
        tool_blocks_map: dict[str, ContentBlock],
        send_message_callback: SendMessageFunctionType,
        start_time: float,
    ) -> tuple[Message, float]: ...


class ChainEventHandler(Protocol):
    """链事件处理器协议

    契约：
    - 输入：事件数据、代理消息、回调函数、开始时间等
    - 输出：更新后的消息和时间
    - 副作用：处理链事件
    - 失败语义：如果处理失败，返回原始消息和时间
    """
    async def __call__(
        self,
        event: dict[str, Any],
        agent_message: Message,
        send_message_callback: SendMessageFunctionType,
        send_token_callback: OnTokenFunctionType | None,
        start_time: float,
        *,
        had_streaming: bool = False,
        message_id: str | None = None,
    ) -> tuple[Message, float]: ...


EventHandler = ToolEventHandler | ChainEventHandler

# 定义事件类型到处理函数的映射
CHAIN_EVENT_HANDLERS: dict[str, ChainEventHandler] = {
    "on_chain_start": handle_on_chain_start,
    "on_chain_end": handle_on_chain_end,
    "on_chain_stream": handle_on_chain_stream,
    "on_chat_model_stream": handle_on_chain_stream,
}

TOOL_EVENT_HANDLERS: dict[str, ToolEventHandler] = {
    "on_tool_start": handle_on_tool_start,
    "on_tool_end": handle_on_tool_end,
    "on_tool_error": handle_on_tool_error,
}


async def process_agent_events(
    agent_executor: AsyncIterator[dict[str, Any]],
    agent_message: Message,
    send_message_callback: SendMessageFunctionType,
    send_token_callback: OnTokenFunctionType | None = None,
) -> Message:
    """处理代理事件并返回最终输出

    关键路径（三步）：
    1) 初始化工具块映射和开始时间
    2) 遍历并处理每个事件
    3) 返回最终消息

    异常流：处理事件时发生异常会抛出 ExceptionWithMessageError。
    性能瓶颈：大量事件处理时。
    排障入口：异常处理机制。
    
    契约：
    - 输入：代理执行器、代理消息、回调函数
    - 输出：最终的代理消息
    - 副作用：更新消息内容和状态
    - 失败语义：如果处理失败，抛出 ExceptionWithMessageError
    """
    if isinstance(agent_message.properties, dict):
        agent_message.properties.update({"icon": "Bot", "state": "partial"})
    else:
        agent_message.properties.icon = "Bot"
        agent_message.properties.state = "partial"
    # 注意：先持久化初始消息并获取消息 `ID`
    agent_message = await send_message_callback(message=agent_message)
    # 注意：流式场景需保持消息 `ID` 不变
    # 注意：未连接 `Chat Output` 时可能没有 `ID`（`_should_skip_message` 为 `True`）
    initial_message_id = agent_message.get_id()
    try:
        # 实现：建立 `run_id` 到工具内容的映射
        tool_blocks_map: dict[str, ToolContent] = {}
        had_streaming = False
        start_time = perf_counter()

        async for event in agent_executor:
            if event["event"] in TOOL_EVENT_HANDLERS:
                tool_handler = TOOL_EVENT_HANDLERS[event["event"]]
                # 注意：流式阶段用 `skip_db_update=True` 避免频繁写库
                agent_message, start_time = await tool_handler(
                    event, agent_message, tool_blocks_map, send_message_callback, start_time
                )
            elif event["event"] in CHAIN_EVENT_HANDLERS:
                chain_handler = CHAIN_EVENT_HANDLERS[event["event"]]

                # 判断是否为流式事件
                if event["event"] in ("on_chain_stream", "on_chat_model_stream"):
                    had_streaming = True
                    agent_message, start_time = await chain_handler(
                        event,
                        agent_message,
                        send_message_callback,
                        send_token_callback,
                        start_time,
                        had_streaming=had_streaming,
                        message_id=initial_message_id,
                    )
                else:
                    agent_message, start_time = await chain_handler(
                        event, agent_message, send_message_callback, None, start_time, had_streaming=had_streaming
                    )

        agent_message.properties.state = "complete"
        # 注意：最终写库更新完整消息（默认 `skip_db_update=False`）
        agent_message = await send_message_callback(message=agent_message)
    except Exception as e:
        raise ExceptionWithMessageError(agent_message, str(e)) from e
    return await Message.create(**agent_message.model_dump())
