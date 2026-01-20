"""模块名称：OpenAI Tools Agent 组件

本模块封装 LangChain 的 `create_openai_tools_agent`，用于基于工具调用的代理构建。
主要功能包括：构建提示词、绑定工具与 LLM、返回可执行的 runnable。

关键组件：
- `OpenAIToolsAgentComponent`：OpenAI tools 代理的组件化入口

设计背景：在 Langflow 中提供标准化的 OpenAI 工具代理配置方式。
注意事项：`user_prompt` 必须包含 `input` 变量占位符。
"""

from langchain.agents import create_openai_tools_agent
from langchain_core.prompts import ChatPromptTemplate, HumanMessagePromptTemplate, PromptTemplate

from lfx.base.agents.agent import LCToolsAgentComponent
from lfx.inputs.inputs import (
    DataInput,
    HandleInput,
    MultilineInput,
)
from lfx.schema.data import Data


class OpenAIToolsAgentComponent(LCToolsAgentComponent):
    """OpenAI Tools 代理组件。

    契约：输入 `llm/system_prompt/user_prompt/chat_history/tools`；输出可执行 runnable；
    副作用：无；失败语义：`user_prompt` 缺少 `input` 时抛 `ValueError`。
    关键路径：1) 校验 `user_prompt` 2) 组装提示消息 3) 构建代理 runnable。
    决策：使用 `ChatPromptTemplate` 统一系统/用户/历史消息
    问题：代理需要一致的消息格式
    方案：固定消息序列并留出占位符
    代价：提示结构较难被用户完全自定义
    重评：当提供可视化提示编辑器时开放更多自定义入口
    """
    display_name: str = "OpenAI Tools Agent"
    description: str = "Agent that uses tools via openai-tools."
    icon = "LangChain"
    name = "OpenAIToolsAgent"

    inputs = [
        *LCToolsAgentComponent.get_base_inputs(),
        HandleInput(
            name="llm",
            display_name="Language Model",
            input_types=["LanguageModel", "ToolEnabledLanguageModel"],
            required=True,
        ),
        MultilineInput(
            name="system_prompt",
            display_name="System Prompt",
            info="System prompt for the agent.",
            value="You are a helpful assistant",
        ),
        MultilineInput(
            name="user_prompt", display_name="Prompt", info="This prompt must contain 'input' key.", value="{input}"
        ),
        DataInput(name="chat_history", display_name="Chat History", is_list=True, advanced=True),
    ]

    def get_chat_history_data(self) -> list[Data] | None:
        """返回聊天历史数据。

        契约：输入无；输出 `chat_history` 或 `None`；副作用无；失败语义：无。
        关键路径：1) 原样返回字段。
        决策：不做类型转换
        问题：保持历史消息结构
        方案：直接返回
        代价：调用方需自行保证 `Data` 结构正确
        重评：当历史格式固定后下沉校验
        """
        return self.chat_history

    def create_agent_runnable(self):
        """构建 OpenAI tools 代理 runnable。

        契约：输入 `llm/tools/system_prompt/user_prompt`；输出 runnable；副作用无；
        失败语义：缺少 `input` 占位符抛 `ValueError`。
        关键路径：1) 校验 `user_prompt` 2) 组装消息模板 3) 创建工具代理。
        决策：强制 `user_prompt` 包含 `input`
        问题：LangChain 工具代理依赖输入变量
        方案：在构建前进行显式校验
        代价：限制用户自由模板
        重评：当支持自定义输入键时放宽校验
        """
        if "input" not in self.user_prompt:
            msg = "Prompt must contain 'input' key."
            raise ValueError(msg)
        messages = [
            ("system", self.system_prompt),
            ("placeholder", "{chat_history}"),
            HumanMessagePromptTemplate(prompt=PromptTemplate(input_variables=["input"], template=self.user_prompt)),
            ("placeholder", "{agent_scratchpad}"),
        ]
        prompt = ChatPromptTemplate.from_messages(messages)
        return create_openai_tools_agent(self.llm, self.tools, prompt)
