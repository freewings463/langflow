"""
模块名称：字段类型出口与懒加载

本模块集中暴露 `field_typing` 相关类型，并通过懒加载避免引入重依赖或循环依赖。
主要功能包括：
- 统一导出对外类型名（`__all__`）
- 在 `__getattr__` 中按需加载常量类型与 RangeSpec

关键组件：
- `__getattr__`
- `_CONSTANTS_NAMES`

设计背景：类型别名与 LangChain 依赖较多，延迟导入可降低启动成本。
注意事项：未在白名单中的名称会抛 `AttributeError`。
"""

from typing import Any

# 注意：模块级仅暴露 __all__，避免触发重依赖

__all__ = [
    "AgentExecutor",
    "BaseChatMemory",
    "BaseChatModel",
    "BaseDocumentCompressor",
    "BaseLLM",
    "BaseLanguageModel",
    "BaseLoader",
    "BaseMemory",
    "BaseOutputParser",
    "BasePromptTemplate",
    "BaseRetriever",
    "Callable",
    "Chain",
    "ChatPromptTemplate",
    "Code",
    "Data",
    "Document",
    "Embeddings",
    "Input",
    "LanguageModel",
    "NestedDict",
    "Object",
    "Output",
    "PromptTemplate",
    "RangeSpec",
    "Retriever",
    "Text",
    "TextSplitter",
    "Tool",
    "VectorStore",
]

# 注意：来自 constants 模块的名称集合
_CONSTANTS_NAMES = {
    "AgentExecutor",
    "BaseChatMemory",
    "BaseChatModel",
    "BaseDocumentCompressor",
    "BaseLLM",
    "BaseLanguageModel",
    "BaseLoader",
    "BaseMemory",
    "BaseOutputParser",
    "BasePromptTemplate",
    "BaseRetriever",
    "Callable",
    "Chain",
    "ChatPromptTemplate",
    "Code",
    "Data",
    "Document",
    "Embeddings",
    "LanguageModel",
    "NestedDict",
    "Object",
    "PromptTemplate",
    "Retriever",
    "Text",
    "TextSplitter",
    "Tool",
    "VectorStore",
}


def __getattr__(name: str) -> Any:
    """按名称延迟导入字段类型。

    契约：
    - 输入：类型名称字符串
    - 输出：对应类型对象
    - 副作用：触发模块导入
    - 失败语义：未知名称抛 `AttributeError`
    """
    if name == "Input":
        from lfx.template.field.base import Input

        return Input
    if name == "Output":
        from lfx.template.field.base import Output

        return Output
    if name == "RangeSpec":
        from .range_spec import RangeSpec

        return RangeSpec
    if name in _CONSTANTS_NAMES:
        from . import constants

        return getattr(constants, name)

    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
