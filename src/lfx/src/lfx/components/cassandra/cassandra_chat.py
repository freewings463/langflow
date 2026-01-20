"""
模块名称：Cassandra 聊天记忆组件

本模块提供基于 Cassandra/Astra DB 的聊天历史存取组件。
主要功能包括：
- 初始化 Cassandra/Astra 连接
- 返回 LangChain `CassandraChatMessageHistory` 以持久化消息

关键组件：
- CassandraChatMemory：聊天记忆组件

设计背景：为 Langflow 提供可持久化的对话记忆后端。
注意事项：依赖 `cassio` 包；需提供 `keyspace` 与 `table_name`。
"""

from lfx.base.memory.model import LCChatMemoryComponent
from lfx.field_typing.constants import Memory
from lfx.inputs.inputs import DictInput, MessageTextInput, SecretStrInput


class CassandraChatMemory(LCChatMemoryComponent):
    """Cassandra 聊天记忆组件。

    契约：必须提供 `database_ref`、`keyspace` 与 `table_name`。
    副作用：初始化 cassio 连接并访问 Cassandra。
    失败语义：缺少 `cassio` 依赖时抛 `ImportError`。
    """

    display_name = "Cassandra Chat Memory"
    description = "Retrieves and store chat messages from Apache Cassandra."
    name = "CassandraChatMemory"
    icon = "Cassandra"

    inputs = [
        MessageTextInput(
            name="database_ref",
            display_name="Contact Points / Astra Database ID",
            info="Contact points for the database (or Astra DB database ID)",
            required=True,
        ),
        MessageTextInput(
            name="username", display_name="Username", info="Username for the database (leave empty for Astra DB)."
        ),
        SecretStrInput(
            name="token",
            display_name="Password / Astra DB Token",
            info="User password for the database (or Astra DB token).",
            required=True,
        ),
        MessageTextInput(
            name="keyspace",
            display_name="Keyspace",
            info="Table Keyspace (or Astra DB namespace).",
            required=True,
        ),
        MessageTextInput(
            name="table_name",
            display_name="Table Name",
            info="The name of the table (or Astra DB collection) where vectors will be stored.",
            required=True,
        ),
        MessageTextInput(
            name="session_id", display_name="Session ID", info="Session ID for the message.", advanced=True
        ),
        DictInput(
            name="cluster_kwargs",
            display_name="Cluster arguments",
            info="Optional dictionary of additional keyword arguments for the Cassandra cluster.",
            advanced=True,
            is_list=True,
        ),
    ]

    def build_message_history(self) -> Memory:
        """构建消息历史存储实例。

        关键路径（三步）：
        1) 校验依赖并初始化 cassio 连接。
        2) 根据 `database_ref` 判断 Astra 或自托管模式。
        3) 返回 `CassandraChatMessageHistory` 实例。

        异常流：缺少 `cassio` 依赖时抛 `ImportError`。
        """
        from langchain_community.chat_message_histories import CassandraChatMessageHistory

        try:
            import cassio
        except ImportError as e:
            msg = "Could not import cassio integration package. Please install it with `pip install cassio`."
            raise ImportError(msg) from e

        from uuid import UUID

        database_ref = self.database_ref

        try:
            UUID(self.database_ref)
            is_astra = True
        except ValueError:
            is_astra = False
            if "," in self.database_ref:
                # 注意：不改变字段类型，拆分后使用局部变量
                database_ref = self.database_ref.split(",")

        if is_astra:
            cassio.init(
                database_id=database_ref,
                token=self.token,
                cluster_kwargs=self.cluster_kwargs,
            )
        else:
            cassio.init(
                contact_points=database_ref,
                username=self.username,
                password=self.token,
                cluster_kwargs=self.cluster_kwargs,
            )

        return CassandraChatMessageHistory(
            session_id=self.session_id,
            table_name=self.table_name,
            keyspace=self.keyspace,
        )
