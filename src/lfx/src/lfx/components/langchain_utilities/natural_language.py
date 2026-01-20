"""模块名称：自然语言分句切分组件

本模块封装 LangChain `NLTKTextSplitter`，按自然语言边界进行文本切分。
主要功能包括：设置语言、分隔符、分块大小与重叠，构建切分器实例。

关键组件：
- `NaturalLanguageTextSplitterComponent`：NLTK 切分器组件入口

设计背景：在自然语言文本中保留句子边界与语义连续性。
注意事项：`language` 会转为小写传递，需确保 NLTK 支持对应语言。
"""

from typing import Any

from langchain_text_splitters import NLTKTextSplitter, TextSplitter

from lfx.base.textsplitters.model import LCTextSplitterComponent
from lfx.inputs.inputs import DataInput, IntInput, MessageTextInput
from lfx.utils.util import unescape_string


class NaturalLanguageTextSplitterComponent(LCTextSplitterComponent):
    """自然语言切分组件。

    契约：输入 `data_input/chunk_size/chunk_overlap/separator/language`；输出 `TextSplitter`；
    副作用无；失败语义：不支持的语言会由 NLTK 抛错。
    关键路径：1) 解析分隔符与语言 2) 构建 `NLTKTextSplitter` 3) 由父类执行切分。
    决策：空分隔符回退到 `\\n\\n`
    问题：缺少显式分隔符时需要稳定段落边界
    方案：使用双换行作为默认
    代价：无空行文本分段较粗
    重评：当输入多为短行文本时改为 `\\n`
    """
    display_name = "Natural Language Text Splitter"
    description = "Split text based on natural language boundaries, optimized for a specified language."
    documentation = (
        "https://python.langchain.com/v0.1/docs/modules/data_connection/document_transformers/split_by_token/#nltk"
    )
    name = "NaturalLanguageTextSplitter"
    icon = "LangChain"
    inputs = [
        IntInput(
            name="chunk_size",
            display_name="Chunk Size",
            info="The maximum number of characters in each chunk after splitting.",
            value=1000,
        ),
        IntInput(
            name="chunk_overlap",
            display_name="Chunk Overlap",
            info="The number of characters that overlap between consecutive chunks.",
            value=200,
        ),
        DataInput(
            name="data_input",
            display_name="Input",
            info="The text data to be split.",
            input_types=["Document", "Data"],
            required=True,
        ),
        MessageTextInput(
            name="separator",
            display_name="Separator",
            info='The character(s) to use as a delimiter when splitting text.\nDefaults to "\\n\\n" if left empty.',
        ),
        MessageTextInput(
            name="language",
            display_name="Language",
            info='The language of the text. Default is "English". '
            "Supports multiple languages for better text boundary recognition.",
        ),
    ]

    def get_data_input(self) -> Any:
        """提供切分输入数据。

        契约：输入无；输出 `data_input`；副作用无；失败语义：未设置时返回 `None`。
        关键路径：1) 原样返回字段。
        决策：不进行格式化
        问题：避免破坏 `Document` 元数据
        方案：直接返回
        代价：调用方需自行校验类型
        重评：当统一校验层上线时下沉检查
        """
        return self.data_input

    def build_text_splitter(self) -> TextSplitter:
        """构建 NLTK 切分器。

        契约：输入 `chunk_size/chunk_overlap/separator/language`；输出 `TextSplitter`；副作用无；
        失败语义：`language` 不受支持时抛错。
        关键路径：1) 解析分隔符 2) 标准化语言 3) 初始化切分器。
        决策：语言统一转为小写
        问题：NLTK 语言标识对大小写敏感
        方案：使用 `lower()` 规范化
        代价：无法区分大小写差异
        重评：当引入语言映射表时改为显式映射
        """
        separator = unescape_string(self.separator) if self.separator else "\n\n"
        return NLTKTextSplitter(
            language=self.language.lower() if self.language else "english",
            separator=separator,
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
        )
