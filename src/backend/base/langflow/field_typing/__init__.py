"""
模块名称：`field_typing` 导出门面

本模块提供类型导出与延迟导入入口，主要用于保持历史导入路径稳定。主要功能包括：
- 统一导出 `lfx.field_typing.constants` 的类型别名
- 通过 `__getattr__` 延迟导入 `Input`/`Output`/`RangeSpec` 以规避循环依赖

关键组件：
- `__getattr__`：动态导入与兼容层
- `_import_input_class`/`_import_output_class`：延迟加载入口

设计背景：历史代码依赖 `langflow.field_typing`，需保留兼容导出层
注意事项：访问未知名称会转发到 `constants`，不存在则抛 `AttributeError`
"""

from typing import Any

from lfx.field_typing.constants import (
    AgentExecutor,
    BaseChatMemory,
    BaseChatModel,
    BaseDocumentCompressor,
    BaseLanguageModel,
    BaseLLM,
    BaseLoader,
    BaseMemory,
    BaseOutputParser,
    BasePromptTemplate,
    BaseRetriever,
    Callable,
    Chain,
    ChatPromptTemplate,
    Code,
    Data,
    Document,
    Embeddings,
    LanguageModel,
    NestedDict,
    Object,
    PromptTemplate,
    Retriever,
    Text,
    TextSplitter,
    Tool,
    VectorStore,
)
from lfx.field_typing.range_spec import RangeSpec


def _import_input_class():
    """延迟导入 `Input`，避免模块加载阶段循环依赖。"""
    from lfx.template.field.base import Input

    return Input


def _import_output_class():
    """延迟导入 `Output`，避免模块加载阶段循环依赖。"""
    from lfx.template.field.base import Output

    return Output


def __getattr__(name: str) -> Any:
    """按需暴露类型名称的动态导入入口。

    契约：输入名称字符串；返回对应类型对象；未知名称触发 `AttributeError`。
    关键路径：1) 处理 `Input`/`Output`/`RangeSpec` 2) 其余名称转发至 `constants`。
    失败语义：未命中名称时由 `getattr` 抛 `AttributeError`。
    注意：延迟导入用于规避 `langflow` 与 `lfx` 的循环依赖。
    """
    if name == "Input":
        return _import_input_class()
    if name == "Output":
        return _import_output_class()
    if name == "RangeSpec":
        return RangeSpec
    from . import constants

    return getattr(constants, name)


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
    "LanguageModel",
    "NestedDict",
    "Object",
    "PromptTemplate",
    "RangeSpec",
    "Retriever",
    "Text",
    "TextSplitter",
    "Tool",
    "VectorStore",
]
