"""
模块名称：Text 输入组件

本模块提供基础文本输入组件，主要用于接收用户输入文本并输出 `Message`。
主要功能包括：
- 接收文本并构造 `Message`

关键组件：
- `TextInputComponent`：文本输入组件

设计背景：在非聊天场景下提供简单文本输入能力。
注意事项：不执行持久化，仅返回消息对象。
"""

from lfx.base.io.text import TextComponent
from lfx.io import MultilineInput, Output
from lfx.schema.message import Message


class TextInputComponent(TextComponent):
    """文本输入组件。"""
    display_name = "Text Input"
    description = "Get user text inputs."
    documentation: str = "https://docs.langflow.org/text-input-and-output"
    icon = "type"
    name = "TextInput"

    inputs = [
        MultilineInput(
            name="input_value",
            display_name="Text",
            info="Text to be passed as input.",
        ),
    ]
    outputs = [
        Output(display_name="Output Text", name="text", method="text_response"),
    ]

    def text_response(self) -> Message:
        """构建并返回文本消息。"""
        return Message(
            text=self.input_value,
        )
