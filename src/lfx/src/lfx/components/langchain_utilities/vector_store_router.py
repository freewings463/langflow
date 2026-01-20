"""模块名称：向量库路由代理组件

本模块封装 LangChain 向量库路由代理，基于 `VectorStoreInfo` 在多个向量库间自动路由。
主要功能包括：创建路由工具包与代理执行器。

关键组件：
- `VectorStoreRouterAgentComponent`：向量库路由代理入口

设计背景：多向量库场景下需要自动选择合适的数据源。
注意事项：`vectorstores` 必须提供描述信息，否则路由质量下降。
"""

from langchain.agents import AgentExecutor, create_vectorstore_router_agent
from langchain.agents.agent_toolkits.vectorstore.toolkit import VectorStoreRouterToolkit

from lfx.base.agents.agent import LCAgentComponent
from lfx.inputs.inputs import HandleInput


class VectorStoreRouterAgentComponent(LCAgentComponent):
    """向量库路由代理组件。

    契约：输入 `llm/vectorstores`；输出 `AgentExecutor`；
    副作用：无；失败语义：向量库描述不完整时路由效果下降。
    关键路径：1) 构建 `VectorStoreRouterToolkit` 2) 创建代理执行器。
    决策：使用 LangChain 默认路由代理实现
    问题：避免重复实现路由逻辑
    方案：复用 `create_vectorstore_router_agent`
    代价：可定制性受限
    重评：当需要自定义路由策略时提供自定义 toolkit
    """
    display_name = "VectorStoreRouterAgent"
    description = "Construct an agent from a Vector Store Router."
    name = "VectorStoreRouterAgent"
    legacy: bool = True

    inputs = [
        *LCAgentComponent.get_base_inputs(),
        HandleInput(
            name="llm",
            display_name="Language Model",
            input_types=["LanguageModel"],
            required=True,
        ),
        HandleInput(
            name="vectorstores",
            display_name="Vector Stores",
            input_types=["VectorStoreInfo"],
            is_list=True,
            required=True,
        ),
    ]

    def build_agent(self) -> AgentExecutor:
        """构建向量库路由代理。

        契约：输入 `llm/vectorstores`；输出 `AgentExecutor`；副作用无；
        失败语义：构建失败时抛异常。
        关键路径：1) 构建 toolkit 2) 创建代理。
        决策：路由信息由 `VectorStoreInfo` 提供
        问题：需要结构化描述辅助选择
        方案：使用 toolkit 内置的描述消费逻辑
        代价：描述不准确会导致路由偏差
        重评：当有反馈信号时引入动态更新描述
        """
        toolkit = VectorStoreRouterToolkit(vectorstores=self.vectorstores, llm=self.llm)
        return create_vectorstore_router_agent(llm=self.llm, toolkit=toolkit, **self.get_agent_kwargs())
