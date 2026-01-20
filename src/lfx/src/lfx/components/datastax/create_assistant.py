"""
模块名称：Astra Assistants 创建组件

本模块提供创建 Assistants 的组件封装，返回 Assistant ID。主要功能包括：
- 通过 OpenAI/Astra 客户端创建 assistant
- 输出创建后的 ID

关键组件：
- `AssistantsCreateAssistant`

设计背景：在流程中需要动态创建 Assistant 并传递其 ID。
使用场景：与 Assistants 相关的动态工作流。
注意事项：依赖 OpenAI 客户端与凭证配置。
"""

from lfx.base.astra_assistants.util import get_patched_openai_client
from lfx.custom.custom_component.component_with_cache import ComponentWithCache
from lfx.inputs.inputs import MultilineInput, StrInput
from lfx.log.logger import logger
from lfx.schema.message import Message
from lfx.template.field.base import Output


class AssistantsCreateAssistant(ComponentWithCache):
    """创建 Assistant 的组件

    契约：输入名称/指令/模型；输出 Assistant ID；
    副作用：调用 Assistants API；
    失败语义：API 异常透传。
    关键路径：1) 获取客户端 2) 创建 assistant 3) 返回 ID。
    决策：使用共享客户端缓存以复用配置。
    问题：重复创建会产生多个 assistant。
    方案：由调用方决定是否复用 ID。
    代价：可能产生多余资源。
    重评：当提供 assistant 复用或查找能力时。
    """
    icon = "AstraDB"
    display_name = "Create Assistant"
    description = "Creates an Assistant and returns it's id"
    legacy = True

    inputs = [
        StrInput(
            name="assistant_name",
            display_name="Assistant Name",
            info="Name for the assistant being created",
        ),
        StrInput(
            name="instructions",
            display_name="Instructions",
            info="Instructions for the assistant, think of these as the system prompt.",
        ),
        StrInput(
            name="model",
            display_name="Model name",
            info=(
                "Model for the assistant.\n\n"
                "Environment variables for provider credentials can be set with the Dotenv Component.\n\n"
                "Models are supported via LiteLLM, "
                "see (https://docs.litellm.ai/docs/providers) for supported model names and env vars."
            ),
        ),
        MultilineInput(
            name="env_set",
            display_name="Environment Set",
            info="Dummy input to allow chaining with Dotenv Component.",
        ),
    ]

    outputs = [
        Output(display_name="Assistant ID", name="assistant_id", method="process_inputs"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.client = get_patched_openai_client(self._shared_component_cache)

    def process_inputs(self) -> Message:
        """创建 Assistant 并返回 ID

        契约：返回 `Message` 包含 assistant.id；副作用：远程创建资源；
        失败语义：API 异常透传。
        """
        logger.info(f"env_set is {self.env_set}")
        assistant = self.client.beta.assistants.create(
            name=self.assistant_name,
            instructions=self.instructions,
            model=self.model,
        )
        return Message(text=assistant.id)
