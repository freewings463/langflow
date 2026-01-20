"""
模块名称：字段类型常量与别名

本模块集中定义字段类型相关的常量、类型别名与默认导入字符串。
主要功能包括：
- 尝试导入 LangChain 相关类型，失败时提供占位类型
- 定义 `NestedDict`、`LanguageModel` 等类型别名
- 维护组件支持类型映射与默认导入模板

关键组件：
- `LANGCHAIN_BASE_TYPES`
- `CUSTOM_COMPONENT_SUPPORTED_TYPES`
- `DEFAULT_IMPORT_STRING`

设计背景：组件生成与类型校验需要统一的类型集合，且需兼容无 LangChain 环境。
注意事项：LangChain 不可用时会创建空占位类型。
"""

import importlib.util
from collections.abc import Callable
from typing import Text, TypeAlias, TypeVar

# 注意：安全导入，避免循环依赖
try:
    from langchain.agents.agent import AgentExecutor
    from langchain.chains.base import Chain
    from langchain.memory.chat_memory import BaseChatMemory
    from langchain_core.chat_history import BaseChatMessageHistory
    from langchain_core.document_loaders import BaseLoader
    from langchain_core.documents import Document
    from langchain_core.documents.compressor import BaseDocumentCompressor
    from langchain_core.embeddings import Embeddings
    from langchain_core.language_models import BaseLanguageModel, BaseLLM
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.memory import BaseMemory
    from langchain_core.output_parsers import BaseLLMOutputParser, BaseOutputParser
    from langchain_core.prompts import BasePromptTemplate, ChatPromptTemplate, PromptTemplate
    from langchain_core.retrievers import BaseRetriever
    from langchain_core.tools import BaseTool, Tool
    from langchain_core.vectorstores import VectorStore, VectorStoreRetriever
    from langchain_text_splitters import TextSplitter
except ImportError:
    # 注意：LangChain 不可用时创建占位类型
    class AgentExecutor:
        pass

    class Chain:
        pass

    class BaseChatMemory:
        pass

    class BaseChatMessageHistory:
        pass

    class BaseLoader:
        pass

    class Document:
        pass

    class BaseDocumentCompressor:
        pass

    class Embeddings:
        pass

    class BaseLanguageModel:
        pass

    class BaseLLM:
        pass

    class BaseChatModel:
        pass

    class BaseMemory:
        pass

    class BaseLLMOutputParser:
        pass

    class BaseOutputParser:
        pass

    class BasePromptTemplate:
        pass

    class ChatPromptTemplate:
        pass

    class PromptTemplate:
        pass

    class BaseRetriever:
        pass

    class BaseTool:
        pass

    class Tool:
        pass

    class VectorStore:
        pass

    class VectorStoreRetriever:
        pass

    class TextSplitter:
        pass


# 注意：导入 lfx schema 类型（避免循环依赖）
from lfx.schema.data import Data

# 类型别名
NestedDict: TypeAlias = dict[str, str | dict]
LanguageModel = TypeVar("LanguageModel", BaseLanguageModel, BaseLLM, BaseChatModel)
ToolEnabledLanguageModel = TypeVar("ToolEnabledLanguageModel", BaseLanguageModel, BaseLLM, BaseChatModel)
Memory = TypeVar("Memory", bound=BaseChatMessageHistory)

Retriever = TypeVar(
    "Retriever",
    BaseRetriever,
    VectorStoreRetriever,
)
OutputParser = TypeVar(
    "OutputParser",
    BaseOutputParser,
    BaseLLMOutputParser,
)


class Object:
    """自定义组件的通用对象类型占位。"""


class Code:
    """自定义组件的代码类型占位。"""


# LangChain 基础类型映射
LANGCHAIN_BASE_TYPES = {
    "Chain": Chain,
    "AgentExecutor": AgentExecutor,
    "BaseTool": BaseTool,
    "Tool": Tool,
    "BaseLLM": BaseLLM,
    "BaseLanguageModel": BaseLanguageModel,
    "PromptTemplate": PromptTemplate,
    "ChatPromptTemplate": ChatPromptTemplate,
    "BasePromptTemplate": BasePromptTemplate,
    "BaseLoader": BaseLoader,
    "Document": Document,
    "TextSplitter": TextSplitter,
    "VectorStore": VectorStore,
    "Embeddings": Embeddings,
    "BaseRetriever": BaseRetriever,
    "BaseOutputParser": BaseOutputParser,
    "BaseMemory": BaseMemory,
    "BaseChatMemory": BaseChatMemory,
    "BaseChatModel": BaseChatModel,
    "Memory": Memory,
    "BaseDocumentCompressor": BaseDocumentCompressor,
}

# LangChain 基础类型 + Python 内建类型
CUSTOM_COMPONENT_SUPPORTED_TYPES = {
    **LANGCHAIN_BASE_TYPES,
    "NestedDict": NestedDict,
    "Data": Data,
    "Text": Text,  # noqa: UP019  # 注意：兼容 `typing.Text` 的历史用法
    "Object": Object,
    "Callable": Callable,
    "LanguageModel": LanguageModel,
    "Retriever": Retriever,
}

# 组件代码生成的默认导入字符串
LANGCHAIN_IMPORT_STRING = """from langchain.agents.agent import AgentExecutor
from langchain.chains.base import Chain
from langchain.memory.chat_memory import BaseChatMemory
from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.document_loaders import BaseLoader
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseLanguageModel, BaseLLM
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.memory import BaseMemory
from langchain_core.output_parsers import BaseLLMOutputParser, BaseOutputParser
from langchain_core.prompts import BasePromptTemplate, ChatPromptTemplate, PromptTemplate
from langchain_core.retrievers import BaseRetriever
from langchain_core.documents.compressor import BaseDocumentCompressor
from langchain_core.tools import BaseTool, Tool
from langchain_core.vectorstores import VectorStore, VectorStoreRetriever
from langchain_text_splitters import TextSplitter
"""


DEFAULT_IMPORT_STRING = """

from lfx.io import (
    BoolInput,
    CodeInput,
    DataInput,
    DictInput,
    DropdownInput,
    FileInput,
    FloatInput,
    HandleInput,
    IntInput,
    LinkInput,
    MessageInput,
    MessageTextInput,
    MultilineInput,
    MultilineSecretInput,
    MultiselectInput,
    NestedDictInput,
    Output,
    PromptInput,
    SecretStrInput,
    SliderInput,
    StrInput,
    TableInput,
)
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame
"""

if importlib.util.find_spec("langchain") is not None:
    DEFAULT_IMPORT_STRING = LANGCHAIN_IMPORT_STRING + DEFAULT_IMPORT_STRING
