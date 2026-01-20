"""文本合并组件。

本模块将两段文本按指定分隔符合并为一个消息。
主要功能包括：
- 接收两段文本与分隔符
- 输出合并后的 Message

关键组件：
- CombineTextComponent：文本合并入口

设计背景：旧组件保留以兼容历史流程。
注意事项：空字符串也会被参与拼接。
"""

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output
from lfx.schema.message import Message


class CombineTextComponent(Component):
    """文本合并组件封装。

    契约：输入为 `text1`/`text2`/`delimiter`，输出为 `Message`。
    副作用：更新 `self.status`。
    """
    display_name = "Combine Text"
    description = "Concatenate two text sources into a single text chunk using a specified delimiter."
    icon = "merge"
    name = "CombineText"
    legacy: bool = True
    replacement = ["processing.DataOperations"]

    inputs = [
        MessageTextInput(
            name="text1",
            display_name="First Text",
            info="The first text input to concatenate.",
        ),
        MessageTextInput(
            name="text2",
            display_name="Second Text",
            info="The second text input to concatenate.",
        ),
        MessageTextInput(
            name="delimiter",
            display_name="Delimiter",
            info="A string used to separate the two text inputs. Defaults to a whitespace.",
            value=" ",
        ),
    ]

    outputs = [
        Output(display_name="Combined Text", name="combined_text", method="combine_texts"),
    ]

    def combine_texts(self) -> Message:
        """按分隔符合并两段文本。

        契约：输出为合并后的 `Message`。
        失败语义：无显式异常，依赖输入字段存在。
        """
        combined = self.delimiter.join([self.text1, self.text2])
        self.status = combined
        return Message(text=combined)
