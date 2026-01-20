"""
模块名称：Hugging Face 与 LangChain 模型桥接

本模块提供 Hugging Face `Model` 与 LangChain `BaseChatModel` 的双向适配。
主要功能包括：
- 将 Hugging Face 消息格式转换为 LangChain 消息
- 将 LangChain 的 `ToolCall` 结果映射回 Hugging Face 结构
- 通过桥接类复用 LangChain 模型能力供 smolagents 调用

关键组件：
- LangChainHFModel：实现 `Model` 接口并委托给 LangChain 模型
- _lc_tool_call_to_hf_tool_call：工具调用结构转换
- _hf_tool_to_lc_tool：工具包装转换

设计背景：在不改动两端接口的前提下复用 LangChain 生态能力。
注意事项：当前不支持 `grammar`，传入会抛 `ValueError`。
"""

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolCall
from langchain_core.tools import BaseTool
from smolagents import Model, Tool
from smolagents.models import ChatMessage, ChatMessageToolCall, ChatMessageToolCallDefinition


def _lc_tool_call_to_hf_tool_call(tool_call: ToolCall) -> ChatMessageToolCall:
    """将 LangChain 的 `ToolCall` 转为 Hugging Face 结构。

    输入：`ToolCall`，包含 `name/args/id`。
    输出：`ChatMessageToolCall`，字段与 Hugging Face 协议匹配。
    失败语义：无显式失败，依赖传入对象字段完整性。
    """
    return ChatMessageToolCall(
        function=ChatMessageToolCallDefinition(name=tool_call.name, arguments=tool_call.args),
        id=tool_call.id,
    )


def _hf_tool_to_lc_tool(tool) -> BaseTool:
    """将 Hugging Face `Tool` 转为 LangChain `BaseTool`。

    输入：`Tool`，要求包含 `langchain_tool` 属性。
    输出：`BaseTool`，供 LangChain 运行时直接调用。
    失败语义：缺少 `langchain_tool` 时抛 `ValueError`，调用方应在注册工具时修复。
    """
    if not hasattr(tool, "langchain_tool"):
        msg = "Hugging Face Tool does not have a langchain_tool attribute"
        raise ValueError(msg)
    return tool.langchain_tool


class LangChainHFModel(Model):
    """Hugging Face `Model` 与 LangChain `BaseChatModel` 的桥接实现。

    契约：`chat_model` 必须实现 LangChain `BaseChatModel` 的 `invoke` 行为。
    副作用：调用 LangChain 模型并可能触发工具调用。
    失败语义：由下游模型抛出的异常原样透传，不在此处吞并。
    """

    def __init__(self, chat_model: BaseChatModel, **kwargs):
        """初始化桥接模型。

        输入：`chat_model` 为 LangChain 聊天模型实例；`**kwargs` 透传给 `Model.__init__`。
        输出：无。
        失败语义：无显式失败，错误由基类初始化或调用方传参引发。
        """
        super().__init__(**kwargs)
        self.chat_model = chat_model

    def __call__(
        self,
        messages: list[dict[str, str]],
        stop_sequences: list[str] | None = None,
        grammar: str | None = None,
        tools_to_call_from: list[Tool] | None = None,
        **kwargs,
    ) -> ChatMessage:
        """将 Hugging Face 输入转为 LangChain 调用并输出 Hugging Face 结构。

        契约：`messages` 为 `{"role","content"}` 列表；`stop_sequences` 透传为 `stop`；
        `grammar` 不支持；`tools_to_call_from` 仅用于绑定工具；输出为 `ChatMessage`。
        关键路径（三步）：
        1) 将 `messages` 转成 LangChain 消息列表，并处理角色映射。
        2) 处理可用工具并绑定到 LangChain 模型。
        3) 调用 `invoke` 获取结果并回写为 Hugging Face `ChatMessage`。

        异常流：`grammar` 非空时抛 `ValueError`；下游模型异常原样透传。
        性能瓶颈：主要成本来自下游模型调用；本层仅做结构转换。
        排障入口：检查 `ValueError` 消息 `Grammar is not yet supported.` 与下游模型日志。
        """
        if grammar:
            msg = "Grammar is not yet supported."
            raise ValueError(msg)

        lc_messages = []
        for m in messages:
            role = m["role"]
            content = m["content"]
            if role == "system":
                lc_messages.append(SystemMessage(content=content))
            elif role == "assistant":
                lc_messages.append(AIMessage(content=content))
            else:
                # 决策：未知角色按 `user` 处理
                # 问题：上游可能传入非 `system/assistant/user` 的角色，导致映射失败
                # 方案：统一降级为 `HumanMessage` 以保持对话连续性
                # 代价：可能掩盖上游角色标注错误
                # 重评：当上游开始严格区分新角色时需显式映射
                lc_messages.append(HumanMessage(content=content))

        if tools_to_call_from:
            tools_to_call_from = [_hf_tool_to_lc_tool(tool) for tool in tools_to_call_from]

        model = self.chat_model.bind_tools(tools_to_call_from) if tools_to_call_from else self.chat_model

        result_msg: AIMessage = model.invoke(lc_messages, stop=stop_sequences, **kwargs)

        return ChatMessage(
            role="assistant",
            content=result_msg.content or "",
            tool_calls=[_lc_tool_call_to_hf_tool_call(tool_call) for tool_call in result_msg.tool_calls],
        )


# 用法示例（仅供本地调试）
# if __name__ == "__main__":
#     from langchain_community.tools import DuckDuckGoSearchRun
#     from langchain_openai import ChatOpenAI
#     from rich import rprint
#     from smolagents import CodeAgent

#     # 示例用法
#     model = LangChainHFModel(chat_model=ChatOpenAI(model="gpt-4o-mini"))
#     search_tool = DuckDuckGoSearchRun()
#     hf_tool = Tool.from_langchain(search_tool)

#     code_agent = CodeAgent(
#         model=model,
#         tools=[hf_tool],
#     )
#     rprint(code_agent.run("Search for Langflow on DuckDuckGo and return the first result"))
