"""DataFrame 解析组件。

本模块根据模板将 DataFrame 行转换为文本并拼接输出。
设计背景：旧组件保留以兼容历史流程。
注意事项：模板占位符需与列名匹配。
"""

from lfx.custom.custom_component.component import Component
from lfx.io import DataFrameInput, MultilineInput, Output, StrInput
from lfx.schema.message import Message


class ParseDataFrameComponent(Component):
    """DataFrame 解析组件封装。

    契约：输入为 DataFrame 与模板；输出为 `Message`。
    副作用：更新 `self.status`。
    失败语义：模板字段缺失时可能抛 `KeyError`。
    """
    display_name = "Parse DataFrame"
    description = (
        "Convert a DataFrame into plain text following a specified template. "
        "Each column in the DataFrame is treated as a possible template key, e.g. {col_name}."
    )
    icon = "braces"
    name = "ParseDataFrame"
    legacy = True
    replacement = ["processing.DataFrameOperations", "processing.TypeConverterComponent"]

    inputs = [
        DataFrameInput(name="df", display_name="DataFrame", info="The DataFrame to convert to text rows."),
        MultilineInput(
            name="template",
            display_name="Template",
            info=(
                "The template for formatting each row. "
                "Use placeholders matching column names in the DataFrame, for example '{col1}', '{col2}'."
            ),
            value="{text}",
        ),
        StrInput(
            name="sep",
            display_name="Separator",
            advanced=True,
            value="\n",
            info="String that joins all row texts when building the single Text output.",
        ),
    ]

    outputs = [
        Output(
            display_name="Text",
            name="text",
            info="All rows combined into a single text, each row formatted by the template and separated by `sep`.",
            method="parse_data",
        ),
    ]

    def _clean_args(self):
        """整理默认参数。"""
        dataframe = self.df
        template = self.template or "{text}"
        sep = self.sep or "\n"
        return dataframe, template, sep

    def parse_data(self) -> Message:
        """按模板渲染每行并拼接为单一消息。

        关键路径（三步）：
        1) 整理模板与分隔符；
        2) 逐行渲染并收集文本；
        3) 合并并写入状态。
        """
        dataframe, template, sep = self._clean_args()

        lines = []
        # 实现：逐行格式化
        for _, row in dataframe.iterrows():
            row_dict = row.to_dict()
            text_line = template.format(**row_dict)
            lines.append(text_line)

        result_string = sep.join(lines)
        self.status = result_string
        return Message(text=result_string)
