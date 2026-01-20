"""
模块名称：Astra Assistant 管理组件

本模块封装 Astra Assistants 的交互流程，负责工具封装、线程/助手复用与事件流输出。主要功能包括：
- 按输入构建 AssistantManager 并执行对话
- 支持文件检索并绑定向量库到助手
- 将执行过程转换为可观测的事件流

关键组件：
- `AstraAssistantManager`

设计背景：需要在组件层统一封装 Assistants 交互与事件派发。
使用场景：构建带工具与文件检索能力的智能助手节点。
注意事项：依赖 `astra_assistants` 与 OpenAI 客户端，异常会原样上抛。
"""

import asyncio
from asyncio import to_thread
from typing import TYPE_CHECKING, Any, cast

from astra_assistants.astra_assistants_manager import AssistantManager
from langchain_core.agents import AgentFinish

from lfx.base.agents.events import ExceptionWithMessageError, process_agent_events
from lfx.base.astra_assistants.util import (
    get_patched_openai_client,
    litellm_model_names,
    sync_upload,
    wrap_base_tool_as_tool_interface,
)
from lfx.custom.custom_component.component_with_cache import ComponentWithCache
from lfx.inputs.inputs import DropdownInput, FileInput, HandleInput, MultilineInput
from lfx.log.logger import logger
from lfx.memory import delete_message
from lfx.schema.content_block import ContentBlock
from lfx.schema.message import Message
from lfx.template.field.base import Output
from lfx.utils.constants import MESSAGE_SENDER_AI

if TYPE_CHECKING:
    from lfx.schema.log import SendMessageFunctionType


class AstraAssistantManager(ComponentWithCache):
    """Astra Assistant 管理组件

    契约：输入模型/指令/工具/消息与可选线程、助手 ID；输出助手响应、工具输出与 ID；
    副作用：调用外部 Assistants API、可能创建向量库与写入状态；
    失败语义：工具或 API 异常透传，初始化过程受锁保护避免重复执行。
    关键路径：1) 处理输入与工具封装 2) 构建 AssistantManager 并执行 3) 生成事件流并回填状态。
    决策：使用组件级缓存与锁确保同一运行只初始化一次。
    问题：并发调用可能导致多次初始化与重复请求。
    方案：在 `initialize` 中使用 `asyncio.Lock` 与 `initialized` 标记。
    代价：首次初始化阻塞并发调用。
    重评：当引入外部协程池或任务队列时。
    """
    display_name = "Astra Assistant Agent"
    name = "Astra Assistant Agent"
    description = "Manages Assistant Interactions"
    icon = "AstraDB"
    legacy = True

    inputs = [
        DropdownInput(
            name="model_name",
            display_name="Model",
            advanced=False,
            options=litellm_model_names,
            value="gpt-4o-mini",
        ),
        MultilineInput(
            name="instructions",
            display_name="Agent Instructions",
            info="Instructions for the assistant, think of these as the system prompt.",
        ),
        HandleInput(
            name="input_tools",
            display_name="Tools",
            input_types=["Tool"],
            is_list=True,
            required=False,
            info="These are the tools that the agent can use to help with tasks.",
        ),
        MultilineInput(
            name="user_message", display_name="User Message", info="User message to pass to the run.", tool_mode=True
        ),
        FileInput(
            name="file",
            display_name="File(s) for retrieval",
            list=True,
            info="Files to be sent with the message.",
            required=False,
            show=True,
            file_types=[
                "txt",
                "md",
                "mdx",
                "csv",
                "json",
                "yaml",
                "yml",
                "xml",
                "html",
                "htm",
                "pdf",
                "docx",
                "py",
                "sh",
                "sql",
                "js",
                "ts",
                "tsx",
                "jpg",
                "jpeg",
                "png",
                "bmp",
                "image",
                "zip",
                "tar",
                "tgz",
                "bz2",
                "gz",
                "c",
                "cpp",
                "cs",
                "css",
                "go",
                "java",
                "php",
                "rb",
                "tex",
                "doc",
                "docx",
                "ppt",
                "pptx",
                "xls",
                "xlsx",
                "jsonl",
            ],
        ),
        MultilineInput(
            name="input_thread_id",
            display_name="Thread ID (optional)",
            info="ID of the thread",
            advanced=True,
        ),
        MultilineInput(
            name="input_assistant_id",
            display_name="Assistant ID (optional)",
            info="ID of the assistant",
            advanced=True,
        ),
        MultilineInput(
            name="env_set",
            display_name="Environment Set",
            info="Dummy input to allow chaining with Dotenv Component.",
            advanced=True,
        ),
    ]

    outputs = [
        Output(display_name="Assistant Response", name="assistant_response", method="get_assistant_response"),
        Output(display_name="Tool output", name="tool_output", method="get_tool_output", hidden=True),
        Output(display_name="Thread Id", name="output_thread_id", method="get_thread_id", hidden=True),
        Output(display_name="Assistant Id", name="output_assistant_id", method="get_assistant_id", hidden=True),
        Output(display_name="Vector Store Id", name="output_vs_id", method="get_vs_id", hidden=True),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.lock = asyncio.Lock()
        self.initialized: bool = False
        self._assistant_response: Message = None  # type: ignore[assignment]
        self._tool_output: Message = None  # type: ignore[assignment]
        self._thread_id: Message = None  # type: ignore[assignment]
        self._assistant_id: Message = None  # type: ignore[assignment]
        self._vs_id: Message = None  # type: ignore[assignment]
        self.client = get_patched_openai_client(self._shared_component_cache)
        self.input_tools: list[Any]

    async def get_assistant_response(self) -> Message:
        """获取助手最终响应

        契约：返回 `_assistant_response` 对应的 `Message`；
        副作用：将响应写入 `self.status`；
        失败语义：初始化或执行异常透传。
        """
        await self.initialize()
        self.status = self._assistant_response
        return self._assistant_response

    async def get_vs_id(self) -> Message:
        """返回文件检索向量库 ID

        契约：返回 `_vs_id` 对应 `Message`，可能为空；
        副作用：更新 `self.status`；
        失败语义：初始化或执行异常透传。
        """
        await self.initialize()
        self.status = self._vs_id
        return self._vs_id

    async def get_tool_output(self) -> Message:
        """返回工具输出或决策标记

        契约：输出 `_tool_output`；当存在 `decision` 时返回其完成标记；
        副作用：更新 `self.status`；
        失败语义：初始化或执行异常透传。
        """
        await self.initialize()
        self.status = self._tool_output
        return self._tool_output

    async def get_thread_id(self) -> Message:
        """返回线程 ID 输出

        契约：返回 `_thread_id` 对应的 `Message`；
        副作用：更新 `self.status`；
        失败语义：初始化或执行异常透传。
        """
        await self.initialize()
        self.status = self._thread_id
        return self._thread_id

    async def get_assistant_id(self) -> Message:
        """返回助手 ID 输出

        契约：返回 `_assistant_id` 对应的 `Message`；
        副作用：更新 `self.status`；
        失败语义：初始化或执行异常透传。
        """
        await self.initialize()
        self.status = self._assistant_id
        return self._assistant_id

    async def initialize(self) -> None:
        """初始化并处理输入

        契约：只执行一次 `process_inputs`；副作用：更新内部缓存；
        失败语义：处理过程异常透传。
        决策：使用锁与 `initialized` 标志防止重复执行。
        """
        async with self.lock:
            if not self.initialized:
                await self.process_inputs()
                self.initialized = True

    async def process_inputs(self) -> None:
        """处理输入并执行 Assistants 交互

        契约：读取组件输入、执行运行并回填输出 `Message`；
        副作用：可能上传文件、创建向量库、写入 `self.status`；
        失败语义：API/工具异常透传，删除消息失败会再次抛出。
        关键路径（三步）：
        1) 封装工具与线程/助手上下文
        2) 执行 AssistantManager 并生成事件流
        3) 写入响应与 ID 到缓存字段
        排障入口：日志与 `process_agent_events` 事件流。
        """
        await logger.ainfo(f"env_set is {self.env_set}")
        await logger.ainfo(self.input_tools)
        tools = []
        tool_obj = None
        if self.input_tools is None:
            self.input_tools = []
        for tool in self.input_tools:
            tool_obj = wrap_base_tool_as_tool_interface(tool)
            tools.append(tool_obj)

        assistant_id = None
        thread_id = None
        if self.input_assistant_id:
            assistant_id = self.input_assistant_id
        if self.input_thread_id:
            thread_id = self.input_thread_id

        if hasattr(self, "graph"):
            session_id = self.graph.session_id
        elif hasattr(self, "_session_id"):
            session_id = self._session_id
        else:
            session_id = None

        agent_message = Message(
            sender=MESSAGE_SENDER_AI,
            sender_name=self.display_name or "Astra Assistant",
            properties={"icon": "Bot", "state": "partial"},
            content_blocks=[ContentBlock(title="Assistant Steps", contents=[])],
            session_id=session_id,
        )

        assistant_manager = AssistantManager(
            instructions=self.instructions,
            model=self.model_name,
            name="managed_assistant",
            tools=tools,
            client=self.client,
            thread_id=thread_id,
            assistant_id=assistant_id,
        )

        if self.file:
            file = await to_thread(sync_upload, self.file, assistant_manager.client)
            vector_store = assistant_manager.client.beta.vector_stores.create(name="my_vs", file_ids=[file.id])
            assistant_tools = assistant_manager.assistant.tools
            assistant_tools += [{"type": "file_search"}]
            assistant = assistant_manager.client.beta.assistants.update(
                assistant_manager.assistant.id,
                tools=assistant_tools,
                tool_resources={"file_search": {"vector_store_ids": [vector_store.id]}},
            )
            assistant_manager.assistant = assistant

        async def step_iterator():
            # 注意：事件名与结构需与 `process_agent_events` 期望一致
            yield {"event": "on_chain_start", "name": "AstraAssistant", "data": {"input": {"text": self.user_message}}}

            content = self.user_message
            result = await assistant_manager.run_thread(content=content, tool=tool_obj)

            if "output" in result and "arguments" in result:
                yield {"event": "on_tool_start", "name": "tool", "data": {"input": {"text": str(result["arguments"])}}}
                yield {"event": "on_tool_end", "name": "tool", "data": {"output": result["output"]}}

            if "file_search" in result and result["file_search"] is not None:
                yield {"event": "on_tool_start", "name": "tool", "data": {"input": {"text": self.user_message}}}
                file_search_str = ""
                for chunk in result["file_search"].to_dict().get("chunks", []):
                    file_search_str += f"## Chunk ID: `{chunk['chunk_id']}`\n"
                    file_search_str += f"**Content:**\n\n```\n{chunk['content']}\n```\n\n"
                    if "score" in chunk:
                        file_search_str += f"**Score:** {chunk['score']}\n\n"
                    if "file_id" in chunk:
                        file_search_str += f"**File ID:** `{chunk['file_id']}`\n\n"
                    if "file_name" in chunk:
                        file_search_str += f"**File Name:** `{chunk['file_name']}`\n\n"
                    if "bytes" in chunk:
                        file_search_str += f"**Bytes:** {chunk['bytes']}\n\n"
                    if "search_string" in chunk:
                        file_search_str += f"**Search String:** {chunk['search_string']}\n\n"
                yield {"event": "on_tool_end", "name": "tool", "data": {"output": file_search_str}}

            if "text" not in result:
                msg = f"No text in result, {result}"
                raise ValueError(msg)

            self._assistant_response = Message(text=result["text"])
            if "decision" in result:
                self._tool_output = Message(text=str(result["decision"].is_complete))
            else:
                self._tool_output = Message(text=result["text"])
            self._thread_id = Message(text=assistant_manager.thread.id)
            self._assistant_id = Message(text=assistant_manager.assistant.id)

            # 注意：需按 `AgentFinish` 格式包装以匹配事件处理器约定
            yield {
                "event": "on_chain_end",
                "name": "AstraAssistant",
                "data": {"output": AgentFinish(return_values={"output": result["text"]}, log="")},
            }

        try:
            if hasattr(self, "send_message"):
                processed_result = await process_agent_events(
                    step_iterator(),
                    agent_message,
                    cast("SendMessageFunctionType", self.send_message),
                )
                self.status = processed_result
        except ExceptionWithMessageError as e:
            # 注意：仅在消息已持久化时才删除
            msg_id = e.agent_message.get_id()
            if msg_id:
                await delete_message(id_=msg_id)
            await self._send_message_event(e.agent_message, category="remove_message")
            raise
        except Exception:
            raise
