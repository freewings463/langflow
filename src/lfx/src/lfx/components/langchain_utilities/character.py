"""模块名称：字符级文本切分组件

本模块提供基于字符长度的切分器封装，主要用于在 Langflow 中构造 LangChain 的
`CharacterTextSplitter`。主要功能包括配置分块大小、重叠与分隔符，并将输入交由父类切分。

关键组件：
- `CharacterTextSplitterComponent`：字符级切分器的组件化适配层

设计背景：统一文本切分入口，避免流程里重复构造切分器。
注意事项：`separator` 为空时默认 `\\n\\n`，`chunk_overlap` 过大会放大 token 成本。
"""

from typing import Any

from langchain_text_splitters import CharacterTextSplitter, TextSplitter

from lfx.base.textsplitters.model import LCTextSplitterComponent
from lfx.inputs.inputs import DataInput, IntInput, MessageTextInput
from lfx.utils.util import unescape_string


class CharacterTextSplitterComponent(LCTextSplitterComponent):
    """字符级切分组件。

    契约：输入 `data_input/chunk_size/chunk_overlap/separator`；输出 `TextSplitter`；副作用无；
    失败语义：`separator` 转义失败时按原样使用，可能导致切分粒度异常。
    关键路径：1) 解析分隔符 2) 构造 `CharacterTextSplitter` 3) 由父类执行切分。
    决策：默认分隔符为 `\\n\\n`
    问题：无分隔符时缺少段落边界
    方案：沿用 LangChain 默认以兼容旧流程
    代价：对无空行文本分段较粗
    重评：当输入多为日志行时改为 `\\n`
    """
    display_name = "Character Text Splitter"
    description = "Split text by number of characters."
    documentation = "https://docs.langflow.org/bundles-langchain"
    name = "CharacterTextSplitter"
    icon = "LangChain"

    inputs = [
        IntInput(
            name="chunk_size",
            display_name="Chunk Size",
            info="The maximum length of each chunk.",
            value=1000,
        ),
        IntInput(
            name="chunk_overlap",
            display_name="Chunk Overlap",
            info="The amount of overlap between chunks.",
            value=200,
        ),
        DataInput(
            name="data_input",
            display_name="Input",
            info="The texts to split.",
            input_types=["Document", "Data"],
            required=True,
        ),
        MessageTextInput(
            name="separator",
            display_name="Separator",
            info='The characters to split on.\nIf left empty defaults to "\\n\\n".',
        ),
    ]

    def get_data_input(self) -> Any:
        """提供父类需要的切分输入。

        契约：输入无；输出 `data_input`；副作用无；失败语义：未设置时返回 `None` 由上游处理。
        关键路径：1) 直接返回字段。
        决策：不在此处做类型转换
        问题：避免隐藏的对象复制或序列化成本
        方案：保持原对象引用
        代价：调用方需自行校验类型
        重评：当统一校验器接管时下沉校验
        """
        return self.data_input

    def build_text_splitter(self) -> TextSplitter:
        """构造字符级切分器实例。

        契约：输入 `chunk_size/chunk_overlap/separator`；输出 `CharacterTextSplitter`；副作用无；
        失败语义：`separator` 非法转义会回落到原字符串。
        关键路径：1) 解析分隔符 2) 初始化切分器 3) 返回供父类调用。
        决策：先解码转义再构造
        问题：UI 输入可能包含 `\\n` 等转义序列
        方案：使用 `unescape_string` 统一处理
        代价：原始反斜杠不可见
        重评：当 UI 改为原生多行输入时取消解码
        """
        separator = unescape_string(self.separator) if self.separator else "\n\n"
        return CharacterTextSplitter(
            chunk_overlap=self.chunk_overlap,
            chunk_size=self.chunk_size,
            separator=separator,
        )
