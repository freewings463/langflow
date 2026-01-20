"""
模块名称：AstraDB 聊天记忆组件

本模块提供基于 Astra DB 的聊天历史存取能力，作为记忆组件接入 LFX。主要功能包括：
- 构建 AstraDB 聊天历史存储对象
- 通过 session_id 读取/写入消息历史

关键组件：
- `AstraDBChatMemory`

设计背景：需要将对话历史持久化到 Astra DB 以支持多轮对话。
使用场景：对话链条中的记忆组件。
注意事项：依赖 `langchain-astradb`，缺失将抛 `ImportError`。
"""

from lfx.base.datastax.astradb_base import AstraDBBaseComponent
from lfx.base.memory.model import LCChatMemoryComponent
from lfx.field_typing.constants import Memory
from lfx.inputs.inputs import MessageTextInput


class AstraDBChatMemory(AstraDBBaseComponent, LCChatMemoryComponent):
    """AstraDB 聊天记忆组件

    契约：输入连接参数与可选 `session_id`；输出 `Memory`；
    副作用：访问 Astra DB；失败语义：依赖缺失抛 `ImportError`。
    关键路径：1) 解析连接参数 2) 构建 `AstraDBChatMessageHistory`。
    决策：允许覆盖 `session_id` 以支持跨会话读取。
    问题：同一组件需支持不同会话上下文。
    方案：将 `session_id` 作为输入字段。
    代价：错误的 session_id 可能导致历史错配。
    重评：当会话由框架强制绑定时。
    """
    display_name = "Astra DB Chat Memory"
    description = "Retrieves and stores chat messages from Astra DB."
    name = "AstraDBChatMemory"
    icon: str = "AstraDB"

    inputs = [
        *AstraDBBaseComponent.inputs,
        MessageTextInput(
            name="session_id",
            display_name="Session ID",
            info="The session ID of the chat. If empty, the current session ID parameter will be used.",
            advanced=True,
        ),
    ]

    def build_message_history(self) -> Memory:
        """构建 AstraDB 聊天历史对象

        契约：返回 `AstraDBChatMessageHistory`；副作用：可能建立远程连接；
        失败语义：依赖缺失抛 `ImportError`。
        关键路径：导入依赖并实例化历史对象。
        决策：直接透传基础连接参数。
        问题：需要统一使用基类提供的 endpoint 与 namespace。
        方案：调用 `get_api_endpoint` 与 `get_keyspace`。
        代价：参数配置错误会导致连接失败。
        重评：当引入集中式连接配置时。
        """
        try:
            from langchain_astradb.chat_message_histories import AstraDBChatMessageHistory
        except ImportError as e:
            msg = (
                "Could not import langchain Astra DB integration package. "
                "Please install it with `uv pip install langchain-astradb`."
            )
            raise ImportError(msg) from e

        return AstraDBChatMessageHistory(
            session_id=self.session_id,
            collection_name=self.collection_name,
            token=self.token,
            api_endpoint=self.get_api_endpoint(),
            namespace=self.get_keyspace(),
            environment=self.environment,
        )
