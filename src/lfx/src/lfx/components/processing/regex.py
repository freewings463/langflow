"""正则提取组件。

本模块基于正则表达式从文本中提取匹配结果。
设计背景：旧组件保留以兼容历史流程。
注意事项：正则错误会返回错误信息 Data。
"""

import re

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output
from lfx.schema.data import Data
from lfx.schema.message import Message


class RegexExtractorComponent(Component):
    """正则提取组件封装。

    契约：输入为文本与正则；输出为 Data 列表或文本消息。
    副作用：更新 `self.status`。
    失败语义：正则语法错误返回错误 Data。
    """
    display_name = "Regex Extractor"
    description = "Extract patterns from text using regular expressions."
    icon = "regex"
    legacy = True
    replacement = ["processing.ParserComponent"]

    inputs = [
        MessageTextInput(
            name="input_text",
            display_name="Input Text",
            info="The text to analyze",
            required=True,
        ),
        MessageTextInput(
            name="pattern",
            display_name="Regex Pattern",
            info="The regular expression pattern to match",
            value=r"",
            required=True,
            tool_mode=True,
        ),
    ]

    outputs = [
        Output(display_name="Data", name="data", method="extract_matches"),
        Output(display_name="Message", name="text", method="get_matches_text"),
    ]

    def extract_matches(self) -> list[Data]:
        """执行正则匹配并返回结果列表。"""
        if not self.pattern or not self.input_text:
            self.status = []
            return []

        try:
            pattern = re.compile(self.pattern)

            matches = pattern.findall(self.input_text)

            # 注意：过滤空匹配
            filtered_matches = [match for match in matches if match]

            result: list = [] if not filtered_matches else [Data(data={"match": match}) for match in filtered_matches]

        except re.error as e:
            error_message = f"Invalid regex pattern: {e!s}"
            result = [Data(data={"error": error_message})]
        except ValueError as e:
            error_message = f"Error extracting matches: {e!s}"
            result = [Data(data={"error": error_message})]

        self.status = result
        return result

    def get_matches_text(self) -> Message:
        """将匹配结果格式化为文本消息。"""
        matches = self.extract_matches()

        if not matches:
            message = Message(text="No matches found")
            self.status = message
            return message

        if "error" in matches[0].data:
            message = Message(text=matches[0].data["error"])
            self.status = message
            return message

        result = "\n".join(match.data["match"] for match in matches)
        message = Message(text=result)
        self.status = message
        return message
