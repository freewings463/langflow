"""
模块名称：Astra Assistants 列表组件

本模块提供获取 Assistants 列表的组件封装，输出 ID 列表。主要功能包括：
- 调用 Assistants API 获取列表
- 以文本形式输出 ID

关键组件：
- `AssistantsListAssistants`

设计背景：需要在流程中选择或复用已有 assistant。
使用场景：列出当前账号下的 assistant ID。
注意事项：依赖 OpenAI 客户端与凭证配置。
"""

from lfx.base.astra_assistants.util import get_patched_openai_client
from lfx.custom.custom_component.component_with_cache import ComponentWithCache
from lfx.schema.message import Message
from lfx.template.field.base import Output


class AssistantsListAssistants(ComponentWithCache):
    """获取 Assistants 列表的组件

    契约：无输入；输出 assistant ID 列表文本；
    副作用：调用 Assistants API；
    失败语义：API 异常透传。
    关键路径：1) 获取列表 2) 提取 ID 3) 拼接输出。
    决策：仅输出 ID 以降低输出体积。
    问题：下游通常只需要 ID 做引用。
    方案：返回 ID 列表字符串。
    代价：缺少名称等元信息。
    重评：当需要展示更多字段时。
    """
    display_name = "List Assistants"
    description = "Returns a list of assistant id's"
    icon = "AstraDB"
    legacy = True
    outputs = [
        Output(display_name="Assistants", name="assistants", method="process_inputs"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.client = get_patched_openai_client(self._shared_component_cache)

    def process_inputs(self) -> Message:
        """列出 assistants 并返回 ID 列表

        契约：返回 `Message` 包含 ID 列表文本；
        副作用：发起网络请求；
        失败语义：API 异常透传。
        """
        assistants = self.client.beta.assistants.list().data
        id_list = [assistant.id for assistant in assistants]
        return Message(
            text="\n".join(id_list)
        )
