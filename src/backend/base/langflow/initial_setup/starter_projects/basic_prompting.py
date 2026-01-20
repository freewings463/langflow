"""
模块名称：基础提示链示例图

本模块构建最简提示链图，用于展示输入 → 提示模板 → 模型 → 输出的最小闭环。主要功能包括：
- 生成包含 `ChatInput`、`PromptComponent`、`OpenAIModelComponent`、`ChatOutput` 的示例图
- 提供可覆写的 `template` 以便快速演示不同风格

关键组件：
- `basic_prompting_graph`: 构建基础提示链 `Graph`

设计背景：新用户需要最小可运行示例理解提示模板与模型连接方式。
注意事项：仅构建图，不执行模型调用；实际运行需配置模型凭据。
"""

from lfx.components.input_output import ChatInput, ChatOutput
from lfx.components.models_and_agents import PromptComponent
from lfx.components.openai.openai_chat_model import OpenAIModelComponent
from lfx.graph import Graph


def basic_prompting_graph(template: str | None = None):
    """构建基础提示链示例图。

    契约：`template=None` 时使用默认海盗风格模板；返回 `Graph` 实例。
    副作用：无外部输入输出，仅组装组件关系。
    失败语义：仅构图不触发模型调用，运行阶段若模型未配置会在执行时失败。
    关键路径：1) 组装输入与模板 2) 绑定模型输入 3) 输出响应。
    决策：默认使用海盗语气模板
    问题：需要在最小示例中体现模板可控性
    方案：提供一个强风格的固定模板并允许外部覆盖
    代价：默认模板具备强风格偏好，可能不符合通用场景
    重评：当默认体验需更中性时替换为通用模板
    """
    if template is None:
        template = """Answer the user as if you were a pirate.

User: {user_input}

Answer:
"""
    chat_input = ChatInput()
    prompt_component = PromptComponent()
    prompt_component.set(
        template=template,
        user_input=chat_input.message_response,
    )

    openai_component = OpenAIModelComponent()
    openai_component.set(input_value=prompt_component.build_prompt)

    chat_output = ChatOutput()
    chat_output.set(input_value=openai_component.text_response)

    return Graph(start=chat_input, end=chat_output)
