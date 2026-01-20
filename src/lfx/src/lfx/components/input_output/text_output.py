"""
模块名称：Text 输出组件

本模块提供文本输出组件，主要用于将输入文本包装为 `Message` 并输出。
主要功能包括：
- 将输入文本输出为消息
- 更新组件状态以便展示

关键组件：
- `TextOutputComponent`：文本输出组件

设计背景：为非聊天场景提供统一输出能力。
注意事项：不会写入聊天历史，仅更新状态。
"""

from lfx.base.io.text import TextComponent
from lfx.io import MultilineInput, Output
from lfx.schema.message import Message


class TextOutputComponent(TextComponent):
    """文本输出组件。"""
    display_name = "Text Output"
    description = "Sends text output via API."
    documentation: str = "https://docs.langflow.org/text-input-and-output"
    icon = "type"
    name = "TextOutput"

    inputs = [
        MultilineInput(
            name="input_value",
            display_name="Inputs",
            info="Text to be passed as output.",
        ),
    ]
    outputs = [
        Output(display_name="Output Text", name="text", method="text_response"),
    ]

    def text_response(self) -> Message:
        """构建文本消息并更新状态。"""
        message = Message(
            text=self.input_value,
        )
        self.status = self.input_value
        return message
