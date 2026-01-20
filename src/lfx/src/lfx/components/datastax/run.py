"""
模块名称：Astra Assistants 运行组件

本模块提供在指定线程上运行 Assistant 的组件封装，支持流式返回。主要功能包括：
- 管理线程 ID（必要时自动创建）
- 调用 runs.create_and_stream 并拼接输出文本

关键组件：
- `AssistantsRun`

设计背景：需要在 LFX 流程中执行 Assistants Run 并获取响应。
使用场景：基于已创建的 assistant/thread 进行一次对话调用。
注意事项：依赖 OpenAI 客户端与有效凭证。
"""

from typing import Any

from openai.lib.streaming import AssistantEventHandler

from lfx.base.astra_assistants.util import get_patched_openai_client
from lfx.custom.custom_component.component_with_cache import ComponentWithCache
from lfx.inputs.inputs import MultilineInput
from lfx.schema.dotdict import dotdict
from lfx.schema.message import Message
from lfx.template.field.base import Output


class AssistantsRun(ComponentWithCache):
    """Assistants 运行组件

    契约：输入 assistant_id/user_message/可选 thread_id；输出响应文本；
    副作用：创建线程、写入消息并触发 run；
    失败语义：API 异常透传。
    关键路径：1) 确保 thread_id 2) 写入用户消息 3) 流式获取响应。
    决策：当未提供 thread_id 时自动创建线程。
    问题：运行需要 thread 上下文。
    方案：缺失时自动创建。
    代价：产生新的线程资源。
    重评：当上游统一管理线程时。
    """
    display_name = "Run Assistant"
    description = "Executes an Assistant Run against a thread"
    icon = "AstraDB"
    legacy = True

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.client = get_patched_openai_client(self._shared_component_cache)
        self.thread_id = None

    def update_build_config(
        self,
        build_config: dotdict,
        field_value: Any,
        field_name: str | None = None,
    ) -> None:
        """更新构建配置并处理 thread_id

        契约：根据输入更新 `build_config`；副作用：可能创建新线程；
        失败语义：API 异常透传。
        决策：当 thread_id 为空时自动创建线程。
        """
        if field_name == "thread_id":
            if field_value is None:
                thread = self.client.beta.threads.create()
                self.thread_id = thread.id
            build_config["thread_id"] = field_value

    inputs = [
        MultilineInput(
            name="assistant_id",
            display_name="Assistant ID",
            info=(
                "The ID of the assistant to run. \n\n"
                "Can be retrieved using the List Assistants component or created with the Create Assistant component."
            ),
        ),
        MultilineInput(
            name="user_message",
            display_name="User Message",
            info="User message to pass to the run.",
        ),
        MultilineInput(
            name="thread_id",
            display_name="Thread ID",
            required=False,
            info="Thread ID to use with the run. If not provided, a new thread will be created.",
        ),
        MultilineInput(
            name="env_set",
            display_name="Environment Set",
            info="Dummy input to allow chaining with Dotenv Component.",
        ),
    ]

    outputs = [Output(display_name="Assistant Response", name="assistant_response", method="process_inputs")]

    def process_inputs(self) -> Message:
        """执行 Assistant Run 并返回响应文本

        契约：返回 `Message` 包含完整响应文本；
        副作用：创建线程、写入消息与流式读取；
        失败语义：API 异常透传。
        关键路径：1) 确保 thread_id 2) 创建用户消息 3) 流式拼接输出。
        排障入口：流式回调异常会直接抛出。
        """
        text = ""

        if self.thread_id is None:
            thread = self.client.beta.threads.create()
            self.thread_id = thread.id

        self.client.beta.threads.messages.create(thread_id=self.thread_id, role="user", content=self.user_message)

        class EventHandler(AssistantEventHandler):
            def __init__(self) -> None:
                super().__init__()

            def on_exception(self, exception: Exception) -> None:
                raise exception

        event_handler = EventHandler()
        with self.client.beta.threads.runs.create_and_stream(
            thread_id=self.thread_id,
            assistant_id=self.assistant_id,
            event_handler=event_handler,
        ) as stream:
            for part in stream.text_deltas:
                text += part
        return Message(text=text)
