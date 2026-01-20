"""模块名称：语言感知递归切分组件

本模块封装按语言规则递归切分的文本切分器，适用于代码/自然语言的结构化分块。
主要功能包括：选择语言枚举、配置分块大小与重叠、构造 `RecursiveCharacterTextSplitter`。

关键组件：
- `LanguageRecursiveTextSplitterComponent`：语言递归切分器的组件化入口

设计背景：在包含代码的场景中保持语义边界与语法完整性。
注意事项：`code_language` 必须匹配 `Language` 枚举值。
"""

from typing import Any

from langchain_text_splitters import Language, RecursiveCharacterTextSplitter, TextSplitter

from lfx.base.textsplitters.model import LCTextSplitterComponent
from lfx.inputs.inputs import DataInput, DropdownInput, IntInput


class LanguageRecursiveTextSplitterComponent(LCTextSplitterComponent):
    """语言感知递归切分组件。

    契约：输入 `data_input/chunk_size/chunk_overlap/code_language`；输出 `TextSplitter`；
    副作用无；失败语义：语言枚举非法时会抛异常。
    关键路径：1) 解析语言枚举 2) 构建递归切分器 3) 由父类执行切分。
    决策：使用 `RecursiveCharacterTextSplitter.from_language`
    问题：不同语言需要不同分隔规则
    方案：交给 LangChain 内置语言模板
    代价：新增语言需要升级依赖
    重评：当语言覆盖不足时引入自定义规则
    """
    display_name: str = "Language Recursive Text Splitter"
    description: str = "Split text into chunks of a specified length based on language."
    documentation: str = "https://docs.langflow.org/bundles-langchain"
    name = "LanguageRecursiveTextSplitter"
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
        DropdownInput(
            name="code_language", display_name="Code Language", options=[x.value for x in Language], value="python"
        ),
    ]

    def get_data_input(self) -> Any:
        """提供切分输入数据。

        契约：输入无；输出 `data_input`；副作用无；失败语义：未设置时返回 `None`。
        关键路径：1) 原样返回字段。
        决策：不做预处理
        问题：保持原始元数据完整
        方案：直接返回
        代价：调用方需自行校验类型
        重评：当统一校验层上线时下沉检查
        """
        return self.data_input

    def build_text_splitter(self) -> TextSplitter:
        """构建语言递归切分器。

        契约：输入 `code_language/chunk_size/chunk_overlap`；输出 `TextSplitter`；副作用无；
        失败语义：非法语言值会抛 `ValueError`。
        关键路径：1) 构造 `Language` 枚举 2) 调用 `from_language` 3) 返回切分器。
        决策：语言枚举直接由输入转换
        问题：避免在组件内维护映射表
        方案：使用 `Language(self.code_language)`
        代价：错误输入会直接抛异常
        重评：当需要更友好提示时改为显式校验
        """
        return RecursiveCharacterTextSplitter.from_language(
            language=Language(self.code_language),
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
        )
