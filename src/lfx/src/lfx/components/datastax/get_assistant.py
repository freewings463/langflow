"""
模块名称：Astra Assistant 查询组件

本模块提供根据 Assistant ID 获取名称的组件封装。主要功能包括：
- 通过客户端检索 assistant 元信息
- 输出 assistant 名称

关键组件：
- `AssistantsGetAssistantName`

设计背景：流程中需要展示或复用已存在的 assistant。
使用场景：根据 ID 获取 assistant 名称。
注意事项：依赖 OpenAI 客户端与有效 ID。
"""

from lfx.base.astra_assistants.util import get_patched_openai_client
from lfx.custom.custom_component.component_with_cache import ComponentWithCache
from lfx.inputs.inputs import MultilineInput, StrInput
from lfx.schema.message import Message
from lfx.template.field.base import Output


class AssistantsGetAssistantName(ComponentWithCache):
    """根据 ID 获取 Assistant 名称的组件

    契约：输入 assistant_id；输出名称 `Message`；
    副作用：调用 Assistants API；
    失败语义：ID 无效或 API 异常透传。
    关键路径：1) 获取客户端 2) retrieve assistant 3) 返回名称。
    决策：仅返回名称，不返回完整对象。
    问题：下游通常仅需要展示名称。
    方案：输出 `assistant.name`。
    代价：无法获取其他字段。
    重评：当下游需要更多元信息时。
    """
    display_name = "Get Assistant name"
    description = "Assistant by id"
    icon = "AstraDB"
    legacy = True
    inputs = [
        StrInput(
            name="assistant_id",
            display_name="Assistant ID",
            info="ID of the assistant",
        ),
        MultilineInput(
            name="env_set",
            display_name="Environment Set",
            info="Dummy input to allow chaining with Dotenv Component.",
        ),
    ]

    outputs = [
        Output(display_name="Assistant Name", name="assistant_name", method="process_inputs"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.client = get_patched_openai_client(self._shared_component_cache)

    def process_inputs(self) -> Message:
        """查询 Assistant 名称

        契约：返回 `Message` 包含 assistant.name；
        副作用：发起网络请求；
        失败语义：API 异常透传。
        """
        assistant = self.client.beta.assistants.retrieve(
            assistant_id=self.assistant_id,
        )
        return Message(text=assistant.name)
