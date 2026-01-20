"""
模块名称：Pass 组件

本模块提供消息透传组件，主要用于在流程中原样转发消息。
主要功能包括：
- 接收并原样输出消息

关键组件：
- `PassMessageComponent`：消息透传组件

设计背景：用于兼容或占位的简单转发节点。
注意事项：组件已标记为 legacy，推荐使用替代组件。
"""

from lfx.custom.custom_component.component import Component
from lfx.io import MessageInput
from lfx.schema.message import Message
from lfx.template.field.base import Output


class PassMessageComponent(Component):
    """原样转发输入消息的组件。"""
    display_name = "Pass"
    description = "Forwards the input message, unchanged."
    name = "Pass"
    icon = "arrow-right"
    legacy: bool = True
    replacement = ["logic.ConditionalRouter"]

    inputs = [
        MessageInput(
            name="input_message",
            display_name="Input Message",
            info="The message to be passed forward.",
            required=True,
        ),
        MessageInput(
            name="ignored_message",
            display_name="Ignored Message",
            info="A second message to be ignored. Used as a workaround for continuity.",
            advanced=True,
        ),
    ]

    outputs = [
        Output(display_name="Output Message", name="output_message", method="pass_message"),
    ]

    def pass_message(self) -> Message:
        """返回原始输入消息并更新状态。"""
        self.status = self.input_message
        return self.input_message
