"""模块名称：SQL 代理组件

本模块封装 LangChain SQL 代理构建逻辑，基于 `SQLDatabaseToolkit` 创建可执行代理。
主要功能包括：从 URI 构建数据库对象、创建工具包并返回代理。

关键组件：
- `SQLAgentComponent`：SQL 代理组件入口

设计背景：在自然语言查询场景提供可执行的 SQL 工具代理。
注意事项：`database_uri` 会直接用于创建连接。
"""

from langchain.agents import AgentExecutor
from langchain_community.agent_toolkits import SQLDatabaseToolkit
from langchain_community.agent_toolkits.sql.base import create_sql_agent
from langchain_community.utilities import SQLDatabase

from lfx.base.agents.agent import LCAgentComponent
from lfx.inputs.inputs import HandleInput, MessageTextInput
from lfx.io import Output


class SQLAgentComponent(LCAgentComponent):
    """SQL 代理组件。

    契约：输入 `llm/database_uri/extra_tools`；输出 `AgentExecutor`；
    副作用：建立数据库连接；失败语义：URI 不合法抛异常。
    关键路径：1) 构建 `SQLDatabase` 2) 创建 toolkit 3) 生成代理。
    决策：将 `max_iterations` 提升到顶层参数
    问题：SQL agent 的参数结构与其他代理不一致
    方案：迁移 `agent_executor_kwargs.max_iterations`
    代价：代码可读性下降
    重评：当上游 API 统一后移除此适配
    """
    display_name = "SQLAgent"
    description = "Construct an SQL agent from an LLM and tools."
    name = "SQLAgent"
    icon = "LangChain"
    inputs = [
        *LCAgentComponent.get_base_inputs(),
        HandleInput(name="llm", display_name="Language Model", input_types=["LanguageModel"], required=True),
        MessageTextInput(name="database_uri", display_name="Database URI", required=True),
        HandleInput(
            name="extra_tools",
            display_name="Extra Tools",
            input_types=["Tool"],
            is_list=True,
            advanced=True,
        ),
    ]

    outputs = [
        Output(display_name="Response", name="response", method="message_response"),
        Output(display_name="Agent", name="agent", method="build_agent", tool_mode=False),
    ]

    def build_agent(self) -> AgentExecutor:
        """构建 SQL 代理执行器。

        契约：输入 `database_uri/llm/extra_tools`；输出 `AgentExecutor`；
        副作用：创建数据库连接；失败语义：连接失败抛异常。
        关键路径：1) 连接数据库 2) 创建工具包 3) 创建代理。
        决策：允许注入额外工具
        问题：SQL 查询常需要配套工具（如时间/权限）
        方案：`extra_tools` 透传给代理创建函数
        代价：工具过多会增加调用开销
        重评：当工具管理规范化后限制白名单
        """
        db = SQLDatabase.from_uri(self.database_uri)
        toolkit = SQLDatabaseToolkit(db=db, llm=self.llm)
        agent_args = self.get_agent_kwargs()
        agent_args["max_iterations"] = agent_args["agent_executor_kwargs"]["max_iterations"]
        del agent_args["agent_executor_kwargs"]["max_iterations"]
        return create_sql_agent(llm=self.llm, toolkit=toolkit, extra_tools=self.extra_tools or [], **agent_args)
