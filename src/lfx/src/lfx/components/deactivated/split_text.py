"""
模块名称：文本切分组件（已停用）

本模块提供将文本按分隔符切分为块的能力，主要用于旧流程中构建检索文档。主要功能包括：
- 将 `Data` 转为 `Document` 并按配置切分
- 返回包含文本与元数据的 `Data` 列表

关键组件：
- `SplitTextComponent`：文本切分组件

设计背景：早期流程需要在组件内完成基础文本切分。
注意事项：`separator` 支持转义字符，使用 `unescape_string` 处理。
"""

from langchain_text_splitters import CharacterTextSplitter

from lfx.custom.custom_component.component import Component
from lfx.io import HandleInput, IntInput, MessageTextInput, Output
from lfx.schema.data import Data
from lfx.utils.util import unescape_string


class SplitTextComponent(Component):
    """文本切分组件。

    契约：输入为 `Data` 列表，输出为切分后的 `Data` 列表。
    失败语义：输入类型不匹配时可能抛异常。
    副作用：更新组件 `status`。
    """
    display_name: str = "Split Text"
    description: str = "Split text into chunks based on specified criteria."
    icon = "scissors-line-dashed"
    name = "SplitText"

    inputs = [
        HandleInput(
            name="data_inputs",
            display_name="Data Inputs",
            info="The data to split.",
            input_types=["Data"],
            is_list=True,
        ),
        IntInput(
            name="chunk_overlap",
            display_name="Chunk Overlap",
            info="Number of characters to overlap between chunks.",
            value=200,
        ),
        IntInput(
            name="chunk_size",
            display_name="Chunk Size",
            info="The maximum number of characters in each chunk.",
            value=1000,
        ),
        MessageTextInput(
            name="separator",
            display_name="Separator",
            info="The character to split on. Defaults to newline.",
            value="\n",
        ),
    ]

    outputs = [
        Output(display_name="Chunks", name="chunks", method="split_text"),
    ]

    def _docs_to_data(self, docs):
        """将 LangChain `Document` 转为 `Data`。

        契约：`doc.page_content` 写入 `Data.text`，`metadata` 写入 `Data.data`。
        失败语义：无。
        副作用：无。
        """
        return [Data(text=doc.page_content, data=doc.metadata) for doc in docs]

    def split_text(self) -> list[Data]:
        """执行文本切分并返回结果。

        契约：使用 `chunk_size`/`chunk_overlap`/`separator` 控制切分。
        失败语义：切分失败由底层抛异常。
        副作用：更新组件 `status`。

        关键路径（三步）：
        1) 将 `Data` 转为 `Document`
        2) 使用 `CharacterTextSplitter` 切分
        3) 转回 `Data` 并返回
        """
        separator = unescape_string(self.separator)

        documents = [_input.to_lc_document() for _input in self.data_inputs if isinstance(_input, Data)]

        splitter = CharacterTextSplitter(
            chunk_overlap=self.chunk_overlap,
            chunk_size=self.chunk_size,
            separator=separator,
        )
        docs = splitter.split_documents(documents)
        data = self._docs_to_data(docs)
        self.status = data
        return data
