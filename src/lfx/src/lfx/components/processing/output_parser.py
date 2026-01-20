"""输出解析器组件。

本模块将 LLM 输出约束为指定格式，并提供格式化指令。
设计背景：旧组件保留以兼容历史流程。
注意事项：当前仅支持 CSV（逗号分隔列表）解析器。
"""

from langchain_core.output_parsers import CommaSeparatedListOutputParser

from lfx.custom.custom_component.component import Component
from lfx.field_typing.constants import OutputParser
from lfx.io import DropdownInput, Output
from lfx.schema.message import Message


class OutputParserComponent(Component):
    """输出解析器组件封装。

    契约：输入为解析器类型；输出为解析器实例与格式指令。
    失败语义：不支持的解析器抛 `ValueError`。
    """
    display_name = "Output Parser"
    description = "Transforms the output of an LLM into a specified format."
    icon = "type"
    name = "OutputParser"
    legacy = True
    replacement = ["processing.StructuredOutput", "processing.ParserComponent"]

    inputs = [
        DropdownInput(
            name="parser_type",
            display_name="Parser",
            options=["CSV"],
            value="CSV",
        ),
    ]

    outputs = [
        Output(
            display_name="Format Instructions",
            name="format_instructions",
            info="Pass to a prompt template to include formatting instructions for LLM responses.",
            method="format_instructions",
        ),
        Output(display_name="Output Parser", name="output_parser", method="build_parser"),
    ]

    def build_parser(self) -> OutputParser:
        """构建输出解析器实例。"""
        if self.parser_type == "CSV":
            return CommaSeparatedListOutputParser()
        msg = "Unsupported or missing parser"
        raise ValueError(msg)

    def format_instructions(self) -> Message:
        """返回解析器的格式化指令文本。"""
        if self.parser_type == "CSV":
            return Message(text=CommaSeparatedListOutputParser().get_format_instructions())
        msg = "Unsupported or missing parser"
        raise ValueError(msg)
