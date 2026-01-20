"""
模块名称：带记忆的聊天机器人示例图

本模块构建“历史记忆 + 当前输入 → 模型回复”的示例图，用于演示对话上下文拼接。主要功能包括：
- 从 `MemoryComponent` 读取历史消息
- 将历史消息转换为模型可读格式并拼接提示

关键组件：
- `memory_chatbot_graph`: 构建带记忆的聊天 `Graph`

设计背景：展示长期对话场景下的上下文管理方式。
注意事项：记忆来源由 `MemoryComponent` 决定，运行时可能为空或受存储配置影响。
"""

from lfx.components.helpers import MemoryComponent
from lfx.components.input_output import ChatInput, ChatOutput
from lfx.components.models_and_agents import PromptComponent
from lfx.components.openai.openai_chat_model import OpenAIModelComponent
from lfx.components.processing.converter import TypeConverterComponent
from lfx.graph import Graph


def memory_chatbot_graph(template: str | None = None):
    """构建带对话记忆的聊天示例图。

    契约：`template=None` 使用默认上下文模板；返回 `Graph` 实例。
    副作用：仅构图；运行时读取历史消息并调用模型。
    失败语义：记忆为空不会中断，但上下文可能为空；模型异常在执行期抛出。
    关键路径：1) 读取历史消息 2) 转换为消息格式 3) 生成与输出回复。
    决策：通过 `TypeConverterComponent` 适配历史消息格式
    问题：历史消息来源结构不稳定，需统一为模型可消费的格式
    方案：将 `DataFrame` 统一转换为消息序列
    代价：转换过程增加一次处理步骤
    重评：当 `MemoryComponent` 原生输出满足要求时移除转换
    """
    if template is None:
        template = """{context}

    User: {user_message}
    AI: """
    memory_component = MemoryComponent()
    chat_input = ChatInput()
    type_converter = TypeConverterComponent()
    type_converter.set(input_data=memory_component.retrieve_messages_dataframe)
    prompt_component = PromptComponent()
    prompt_component.set(
        template=template,
        user_message=chat_input.message_response,
        context=type_converter.convert_to_message,
    )
    openai_component = OpenAIModelComponent()
    openai_component.set(input_value=prompt_component.build_prompt)

    chat_output = ChatOutput()
    chat_output.set(input_value=openai_component.text_response)

    return Graph(chat_input, chat_output)
