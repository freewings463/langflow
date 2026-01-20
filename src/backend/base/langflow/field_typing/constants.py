"""
模块名称：`field_typing` 常量与类型兼容层

本模块统一导出 `lfx.field_typing.constants` 的类型别名，并补充历史兼容类型。主要功能包括：
- 转发 `LangChain` 相关基类与工具类型
- 扩展 `CUSTOM_COMPONENT_SUPPORTED_TYPES` 以兼容旧版自定义组件

关键组件：
- `CUSTOM_COMPONENT_SUPPORTED_TYPES`：自定义组件类型白名单

设计背景：旧代码通过 `langflow.field_typing.constants` 引用类型，迁移后需保持导出路径稳定
注意事项：仅做类型导出与映射补齐，不提供运行时逻辑
"""

from collections.abc import Callable
from typing import Text

from lfx.field_typing.constants import (
    CUSTOM_COMPONENT_SUPPORTED_TYPES,
    DEFAULT_IMPORT_STRING,
    LANGCHAIN_BASE_TYPES,
    AgentExecutor,
    BaseChatMemory,
    BaseChatMessageHistory,
    BaseChatModel,
    BaseDocumentCompressor,
    BaseLanguageModel,
    BaseLLM,
    BaseLLMOutputParser,
    BaseLoader,
    BaseMemory,
    BaseOutputParser,
    BasePromptTemplate,
    BaseRetriever,
    BaseTool,
    Chain,
    ChatPromptTemplate,
    Code,
    Document,
    Embeddings,
    LanguageModel,
    Memory,
    NestedDict,
    Object,
    OutputParser,
    PromptTemplate,
    Retriever,
    TextSplitter,
    Tool,
    ToolEnabledLanguageModel,
    VectorStore,
    VectorStoreRetriever,
)

from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame

from langflow.schema.message import Message

# 注意：为兼容旧版自定义组件字段校验，补齐 `Message`/`DataFrame` 类型
CUSTOM_COMPONENT_SUPPORTED_TYPES = {
    **CUSTOM_COMPONENT_SUPPORTED_TYPES,
    "Message": Message,
    "DataFrame": DataFrame,
}

__all__ = [
    "CUSTOM_COMPONENT_SUPPORTED_TYPES",
    "DEFAULT_IMPORT_STRING",
    "LANGCHAIN_BASE_TYPES",
    "AgentExecutor",
    "BaseChatMemory",
    "BaseChatMessageHistory",
    "BaseChatModel",
    "BaseDocumentCompressor",
    "BaseLLM",
    "BaseLLMOutputParser",
    "BaseLanguageModel",
    "BaseLoader",
    "BaseMemory",
    "BaseOutputParser",
    "BasePromptTemplate",
    "BaseRetriever",
    "BaseTool",
    "Callable",
    "Chain",
    "ChatPromptTemplate",
    "Code",
    "Data",
    "DataFrame",
    "Document",
    "Embeddings",
    "LanguageModel",
    "Memory",
    "Message",
    "NestedDict",
    "Object",
    "OutputParser",
    "PromptTemplate",
    "Retriever",
    "Text",
    "TextSplitter",
    "Tool",
    "ToolEnabledLanguageModel",
    "VectorStore",
    "VectorStoreRetriever",
]
