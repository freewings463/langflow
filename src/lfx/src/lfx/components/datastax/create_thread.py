"""
模块名称：Astra Assistants 线程创建组件

本模块提供创建 Assistant 线程的组件封装，返回 Thread ID。主要功能包括：
- 通过客户端创建新的 thread
- 输出 thread ID 供后续运行复用

关键组件：
- `AssistantsCreateThread`

设计背景：Assistants 运行需要 thread 上下文。
使用场景：在流程中显式创建并传递 thread ID。
注意事项：依赖 OpenAI 客户端与凭证配置。
"""

from lfx.base.astra_assistants.util import get_patched_openai_client
from lfx.custom.custom_component.component_with_cache import ComponentWithCache
from lfx.inputs.inputs import MultilineInput
from lfx.schema.message import Message
from lfx.template.field.base import Output


class AssistantsCreateThread(ComponentWithCache):
    """创建 Assistants 线程的组件

    契约：输入环境占位参数；输出线程 ID；
    副作用：调用 Assistants API；
    失败语义：API 异常透传。
    关键路径：1) 获取客户端 2) 创建 thread 3) 返回 ID。
    决策：通过独立组件创建线程以便复用。
    问题：线程 ID 在多步流程中需要显式传递。
    方案：提供组件输出线程 ID。
    代价：额外组件步骤。
    重评：当框架自动管理线程上下文时。
    """
    display_name = "Create Assistant Thread"
    description = "Creates a thread and returns the thread id"
    icon = "AstraDB"
    legacy = True
    inputs = [
        MultilineInput(
            name="env_set",
            display_name="Environment Set",
            info="Dummy input to allow chaining with Dotenv Component.",
        ),
    ]

    outputs = [
        Output(display_name="Thread ID", name="thread_id", method="process_inputs"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.client = get_patched_openai_client(self._shared_component_cache)

    def process_inputs(self) -> Message:
        """创建线程并返回 ID

        契约：返回 `Message` 包含 thread.id；副作用：远程创建资源；
        失败语义：API 异常透传。
        """
        thread = self.client.beta.threads.create()
        thread_id = thread.id

        return Message(text=thread_id)
