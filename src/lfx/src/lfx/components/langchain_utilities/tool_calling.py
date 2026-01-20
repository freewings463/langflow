"""模块名称：通用工具调用代理组件

本模块封装 LangChain 的工具调用代理创建逻辑，并对 IBM Granite 模型进行适配增强。
主要功能包括：构建提示模板、校验工具命名、选择默认或 Granite 适配代理。

关键组件：
- `ToolCallingAgentComponent`：工具调用代理组件入口

设计背景：在一个组件中统一处理通用代理与 Granite 平台差异。
注意事项：Granite 模型会触发提示增强与专用代理构建逻辑。
"""

from langchain.agents import create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate

from lfx.base.agents.agent import LCToolsAgentComponent

# IBM Granite 专用逻辑独立在适配模块
from lfx.components.langchain_utilities.ibm_granite_handler import (
    create_granite_agent,
    get_enhanced_system_prompt,
    is_granite_model,
)
from lfx.inputs.inputs import (
    DataInput,
    HandleInput,
    MessageTextInput,
)
from lfx.schema.data import Data


class ToolCallingAgentComponent(LCToolsAgentComponent):
    """通用工具调用代理组件。

    契约：输入 `llm/system_prompt/tools/chat_history`；输出 runnable；
    副作用：可能写入 `_effective_system_prompt`；失败语义：不支持工具调用时抛 `NotImplementedError`。
    关键路径：1) 组装提示模板 2) 校验工具名称 3) 构建默认或 Granite 代理。
    决策：Granite 模型走专用代理
    问题：WatsonX 平台工具调用行为与默认实现不一致
    方案：检测模型并切换到 `create_granite_agent`
    代价：增加分支与维护成本
    重评：当平台行为稳定后合并回默认路径
    """
    display_name: str = "Tool Calling Agent"
    description: str = "An agent designed to utilize various tools seamlessly within workflows."
    icon = "LangChain"
    name = "ToolCallingAgent"

    inputs = [
        *LCToolsAgentComponent.get_base_inputs(),
        HandleInput(
            name="llm",
            display_name="Language Model",
            input_types=["LanguageModel"],
            required=True,
            info="Language model that the agent utilizes to perform tasks effectively.",
        ),
        MessageTextInput(
            name="system_prompt",
            display_name="System Prompt",
            info="System prompt to guide the agent's behavior.",
            value="You are a helpful assistant that can use tools to answer questions and perform tasks.",
        ),
        DataInput(
            name="chat_history",
            display_name="Chat Memory",
            is_list=True,
            advanced=True,
            info="This input stores the chat history, allowing the agent to remember previous conversations.",
        ),
    ]

    def get_chat_history_data(self) -> list[Data] | None:
        """返回聊天历史数据。

        契约：输入无；输出 `chat_history` 或 `None`；副作用无；失败语义：无。
        关键路径：1) 原样返回字段。
        决策：不进行格式转换
        问题：保持历史消息结构
        方案：直接返回
        代价：调用方需保证数据类型
        重评：当历史格式固定后下沉校验
        """
        return self.chat_history

    def create_agent_runnable(self):
        """构建工具调用代理 runnable。

        关键路径（三步）：
        1) 计算有效系统提示并注入历史占位符
        2) 构建 `ChatPromptTemplate` 与校验工具名
        3) 选择 Granite 适配或默认代理

        异常流：模型不支持工具调用时抛 `NotImplementedError`。
        排障入口：异常信息包含组件名。
        决策：仅在系统提示非空时加入 system 消息
        问题：空系统提示会引入无意义消息
        方案：对 `strip()` 做判断
        代价：无法通过空提示显式禁用 system
        重评：当需要显式空 system 时改为配置开关
        """
        messages = []

        # 使用局部变量避免重复调用时修改组件状态
        effective_system_prompt = self.system_prompt or ""

        # Granite 模型需要更明确的工具使用说明
        if is_granite_model(self.llm) and self.tools:
            effective_system_prompt = get_enhanced_system_prompt(effective_system_prompt, self.tools)
            # 缓存增强后的提示，供下游使用但不修改原始字段
            self._effective_system_prompt = effective_system_prompt

        # 仅在 system prompt 非空时加入 system 消息
        if effective_system_prompt.strip():
            messages.append(("system", "{system_prompt}"))

        messages.extend(
            [
                ("placeholder", "{chat_history}"),
                ("human", "{input}"),
                ("placeholder", "{agent_scratchpad}"),
            ]
        )

        prompt = ChatPromptTemplate.from_messages(messages)
        self.validate_tool_names()

        try:
            # Granite 模型走专用代理，其它模型走默认路径
            if is_granite_model(self.llm) and self.tools:
                return create_granite_agent(self.llm, self.tools, prompt)

            # 默认路径（包含非 Granite 的 WatsonX 模型）
            return create_tool_calling_agent(self.llm, self.tools or [], prompt)
        except NotImplementedError as e:
            message = f"{self.display_name} does not support tool calling. Please try using a compatible model."
            raise NotImplementedError(message) from e
