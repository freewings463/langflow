"""
模块名称：代码块提取组件（已停用）

本模块提供从文本中提取 Markdown 代码块的能力，主要用于从模型输出中抽取可执行片段。主要功能包括：
- 识别以 ``` 或 ```语言 开始的代码块
- 返回首个匹配的代码块内容

关键组件：
- `CodeBlockExtractor`：代码块提取组件

设计背景：在旧流程中用于从聊天输出中提取代码片段。
注意事项：仅匹配文首代码块，且需要闭合的 ``` 结束。
"""

import re

from lfx.custom.custom_component.component import Component
from lfx.field_typing import Input, Output, Text


class CodeBlockExtractor(Component):
    """代码块提取组件。

    契约：输入为文本字符串；返回首个匹配的代码块内容。
    失败语义：未匹配时返回空字符串。
    副作用：无。
    """

    display_name = "Code Block Extractor"
    description = "Extracts code block from text."
    name = "CodeBlockExtractor"

    inputs = [Input(name="text", field_type=Text, description="Text to extract code blocks from.")]

    outputs = [Output(name="code_block", display_name="Code Block", method="get_code_block")]

    def get_code_block(self) -> Text:
        """提取 Markdown 代码块内容。

        契约：仅匹配以 ``` 开始并以 ``` 结束的首个代码块。
        失败语义：未命中返回空字符串。
        副作用：无。
        """
        text = self.text.strip()
        # 注意：支持 ``` 或 ```language 的起始形式
        pattern = r"^```(?:\w+)?\s*\n(.*?)(?=^```)```"
        match = re.search(pattern, text, re.MULTILINE)
        code_block = ""
        if match:
            code_block = match.group(1)
        return code_block
