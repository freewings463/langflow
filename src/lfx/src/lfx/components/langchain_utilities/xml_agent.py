"""模块名称：XML 工具代理组件

本模块封装 LangChain XML 工具代理创建逻辑，通过 XML 标签格式指导 LLM 调用工具。
主要功能包括：构建提示模板、绑定工具与 LLM、返回 runnable。

关键组件：
- `XMLAgentComponent`：XML 工具代理组件入口

设计背景：在工具调用场景中使用可解析的 XML 格式约束模型输出。
注意事项：`user_prompt` 必须包含 `input` 占位符。
"""

from langchain.agents import create_xml_agent
from langchain_core.prompts import ChatPromptTemplate, HumanMessagePromptTemplate, PromptTemplate

from lfx.base.agents.agent import LCToolsAgentComponent
from lfx.inputs.inputs import (
    DataInput,
    HandleInput,
    MultilineInput,
)
from lfx.schema.data import Data


class XMLAgentComponent(LCToolsAgentComponent):
    """XML 工具代理组件。

    契约：输入 `llm/system_prompt/user_prompt/tools/chat_history`；输出 runnable；
    副作用：无；失败语义：`user_prompt` 缺少 `input` 时抛 `ValueError`。
    关键路径：1) 校验提示 2) 构建消息模板 3) 创建 XML 代理。
    决策：系统提示内置 XML 工具调用格式
    问题：需要稳定的可解析输出结构
    方案：在系统提示中固定 XML 标签约束
    代价：模型输出更受限
    重评：当支持 JSON 工具调用时考虑改为 JSON 格式
    """
    display_name: str = "XML Agent"
    description: str = "Agent that uses tools formatting instructions as xml to the Language Model."
    icon = "LangChain"
    beta = True
    name = "XMLAgent"
    inputs = [
        *LCToolsAgentComponent.get_base_inputs(),
        HandleInput(name="llm", display_name="Language Model", input_types=["LanguageModel"], required=True),
        DataInput(name="chat_history", display_name="Chat History", is_list=True, advanced=True),
        MultilineInput(
            name="system_prompt",
            display_name="System Prompt",
            info="System prompt for the agent.",
            value="""You are a helpful assistant. Help the user answer any questions.

You have access to the following tools:

{tools}

In order to use a tool, you can use <tool></tool> and <tool_input></tool_input> tags. You will then get back a response in the form <observation></observation>

For example, if you have a tool called 'search' that could run a google search, in order to search for the weather in SF you would respond:

<tool>search</tool><tool_input>weather in SF</tool_input>

<observation>64 degrees</observation>

When you are done, respond with a final answer between <final_answer></final_answer>. For example:

<final_answer>The weather in SF is 64 degrees</final_answer>

Begin!

Question: {input}

{agent_scratchpad}
            """,  # noqa: E501
        ),
        MultilineInput(
            name="user_prompt", display_name="Prompt", info="This prompt must contain 'input' key.", value="{input}"
        ),
    ]

    def get_chat_history_data(self) -> list[Data] | None:
        """返回聊天历史数据。

        契约：输入无；输出 `chat_history` 或 `None`；副作用无；失败语义：无。
        关键路径：1) 原样返回字段。
        决策：不进行格式转换
        问题：保持历史消息结构
        方案：直接返回
        代价：调用方需保证数据类型正确
        重评：当历史格式固定后下沉校验
        """
        return self.chat_history

    def create_agent_runnable(self):
        """构建 XML 工具代理 runnable。

        契约：输入 `llm/tools/system_prompt/user_prompt`；输出 runnable；副作用无；
        失败语义：缺少 `input` 占位符抛 `ValueError`。
        关键路径：1) 校验提示 2) 组装消息序列 3) 创建 XML 代理。
        决策：强制 `user_prompt` 包含 `input`
        问题：模板缺失变量会导致运行期失败
        方案：构建前显式校验
        代价：限制用户自定义自由度
        重评：当支持自定义输入键时放宽校验
        """
        if "input" not in self.user_prompt:
            msg = "Prompt must contain 'input' key."
            raise ValueError(msg)
        messages = [
            ("system", self.system_prompt),
            ("placeholder", "{chat_history}"),
            HumanMessagePromptTemplate(prompt=PromptTemplate(input_variables=["input"], template=self.user_prompt)),
            ("ai", "{agent_scratchpad}"),
        ]
        prompt = ChatPromptTemplate.from_messages(messages)
        return create_xml_agent(self.llm, self.tools, prompt)
