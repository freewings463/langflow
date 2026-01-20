"""模块名称：递归字符切分组件

本模块封装 LangChain `RecursiveCharacterTextSplitter`，用于尽量保留相关文本的连续性。
主要功能包括：配置分隔符列表、分块大小与重叠，并构建切分器实例。

关键组件：
- `RecursiveCharacterTextSplitterComponent`：递归字符切分组件入口

设计背景：在不同粒度分隔符之间递归切分，提升语义完整性。
注意事项：空分隔符列表将使用 LangChain 默认分隔符序列。
"""

from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter, TextSplitter

from lfx.base.textsplitters.model import LCTextSplitterComponent
from lfx.inputs.inputs import DataInput, IntInput, MessageTextInput
from lfx.utils.util import unescape_string


class RecursiveCharacterTextSplitterComponent(LCTextSplitterComponent):
    """递归字符切分组件。

    契约：输入 `data_input/chunk_size/chunk_overlap/separators`；输出 `TextSplitter`；
    副作用无；失败语义：分隔符转义异常会导致切分粒度偏差。
    关键路径：1) 解析分隔符 2) 构建递归切分器 3) 由父类执行切分。
    决策：空分隔符列表回退到 LangChain 默认
    问题：用户未配置分隔符时仍需可用
    方案：传 `None` 触发默认分隔符
    代价：无法区分用户故意传空列表的意图
    重评：当 UI 支持显式“使用默认”选项时区分处理
    """
    display_name: str = "Recursive Character Text Splitter"
    description: str = "Split text trying to keep all related text together."
    documentation: str = "https://docs.langflow.org/components-processing"
    name = "RecursiveCharacterTextSplitter"
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
            name="separators",
            display_name="Separators",
            info='The characters to split on.\nIf left empty defaults to ["\\n\\n", "\\n", " ", ""].',
            is_list=True,
        ),
    ]

    def get_data_input(self) -> Any:
        """提供切分输入数据。

        契约：输入无；输出 `data_input`；副作用无；失败语义：未设置时返回 `None`。
        关键路径：1) 原样返回字段。
        决策：不在此处做类型转换
        问题：避免丢失 `Document` 元数据
        方案：直接返回引用
        代价：调用方需自行校验类型
        重评：当统一校验器接管时下沉校验
        """
        return self.data_input

    def build_text_splitter(self) -> TextSplitter:
        """构建递归字符切分器。

        契约：输入 `separators/chunk_size/chunk_overlap`；输出 `TextSplitter`；副作用无；
        失败语义：分隔符转义异常时按原字符串使用。
        关键路径：1) 解析分隔符列表 2) 初始化切分器 3) 返回实例。
        决策：仅当列表非空时做转义处理
        问题：避免对 `None` 默认值做不必要处理
        方案：空列表传 `None`
        代价：无法区分显式空列表与未配置
        重评：当提供显式“无分隔符”选项时调整判断
        """
        if not self.separators:
            separators: list[str] | None = None
        else:
            # 若包含转义字符，统一解码处理
            separators = [unescape_string(x) for x in self.separators]

        return RecursiveCharacterTextSplitter(
            separators=separators,
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
        )
