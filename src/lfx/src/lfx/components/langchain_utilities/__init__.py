"""模块名称：LangChain 工具组件导出层

本模块负责按需导入 `langchain_utilities` 目录下的组件，降低启动成本并避免循环依赖。
主要功能包括：维护动态导入表、实现 `__getattr__` 懒加载、暴露 `__all__`。

关键组件：
- `_dynamic_imports`：属性名到模块名的映射
- `__getattr__`：按需导入实现

设计背景：组件数量多且依赖重，需要延迟加载。
注意事项：访问未知属性会抛 `AttributeError`。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lfx.components._importing import import_mod

if TYPE_CHECKING:
    from .character import CharacterTextSplitterComponent
    from .conversation import ConversationChainComponent
    from .csv_agent import CSVAgentComponent
    from .fake_embeddings import FakeEmbeddingsComponent
    from .html_link_extractor import HtmlLinkExtractorComponent
    from .json_agent import JsonAgentComponent
    from .langchain_hub import LangChainHubPromptComponent
    from .language_recursive import LanguageRecursiveTextSplitterComponent
    from .language_semantic import SemanticTextSplitterComponent
    from .llm_checker import LLMCheckerChainComponent
    from .llm_math import LLMMathChainComponent
    from .natural_language import NaturalLanguageTextSplitterComponent
    from .openai_tools import OpenAIToolsAgentComponent
    from .openapi import OpenAPIAgentComponent
    from .recursive_character import RecursiveCharacterTextSplitterComponent
    from .retrieval_qa import RetrievalQAComponent
    from .runnable_executor import RunnableExecComponent
    from .self_query import SelfQueryRetrieverComponent
    from .spider import SpiderTool
    from .sql import SQLAgentComponent
    from .sql_database import SQLDatabaseComponent
    from .sql_generator import SQLGeneratorComponent
    from .tool_calling import ToolCallingAgentComponent
    from .vector_store_info import VectorStoreInfoComponent
    from .vector_store_router import VectorStoreRouterAgentComponent
    from .xml_agent import XMLAgentComponent

_dynamic_imports = {
    "CharacterTextSplitterComponent": "character",
    "ConversationChainComponent": "conversation",
    "CSVAgentComponent": "csv_agent",
    "FakeEmbeddingsComponent": "fake_embeddings",
    "HtmlLinkExtractorComponent": "html_link_extractor",
    "JsonAgentComponent": "json_agent",
    "LangChainHubPromptComponent": "langchain_hub",
    "LanguageRecursiveTextSplitterComponent": "language_recursive",
    "LLMCheckerChainComponent": "llm_checker",
    "LLMMathChainComponent": "llm_math",
    "NaturalLanguageTextSplitterComponent": "natural_language",
    "OpenAIToolsAgentComponent": "openai_tools",
    "OpenAPIAgentComponent": "openapi",
    "RecursiveCharacterTextSplitterComponent": "recursive_character",
    "RetrievalQAComponent": "retrieval_qa",
    "RunnableExecComponent": "runnable_executor",
    "SelfQueryRetrieverComponent": "self_query",
    "SemanticTextSplitterComponent": "language_semantic",
    "SpiderTool": "spider",
    "SQLAgentComponent": "sql",
    "SQLDatabaseComponent": "sql_database",
    "SQLGeneratorComponent": "sql_generator",
    "ToolCallingAgentComponent": "tool_calling",
    "VectorStoreInfoComponent": "vector_store_info",
    "VectorStoreRouterAgentComponent": "vector_store_router",
    "XMLAgentComponent": "xml_agent",
}

__all__ = [
    "CSVAgentComponent",
    "CharacterTextSplitterComponent",
    "ConversationChainComponent",
    "FakeEmbeddingsComponent",
    "HtmlLinkExtractorComponent",
    "JsonAgentComponent",
    "LLMCheckerChainComponent",
    "LLMMathChainComponent",
    "LangChainHubPromptComponent",
    "LanguageRecursiveTextSplitterComponent",
    "NaturalLanguageTextSplitterComponent",
    "OpenAIToolsAgentComponent",
    "OpenAPIAgentComponent",
    "RecursiveCharacterTextSplitterComponent",
    "RetrievalQAComponent",
    "RunnableExecComponent",
    "SQLAgentComponent",
    "SQLDatabaseComponent",
    "SQLGeneratorComponent",
    "SelfQueryRetrieverComponent",
    "SemanticTextSplitterComponent",
    "SpiderTool",
    "ToolCallingAgentComponent",
    "VectorStoreInfoComponent",
    "VectorStoreRouterAgentComponent",
    "XMLAgentComponent",
]


def __getattr__(attr_name: str) -> Any:
    """按需导入组件并缓存到模块命名空间。

    契约：输入属性名；输出组件对象；副作用：写入 `globals()`；
    失败语义：不存在映射或导入失败时抛 `AttributeError`。
    关键路径：1) 校验映射 2) 动态导入 3) 写入缓存并返回。
    决策：使用 `import_mod` 而非直接 `importlib`
    问题：需要统一异常信息与模块路径解析
    方案：复用 `import_mod` 适配层
    代价：增加一层封装依赖
    重评：当导入路径稳定且无特殊处理时可直接使用标准库
    """
    if attr_name not in _dynamic_imports:
        msg = f"module '{__name__}' has no attribute '{attr_name}'"
        raise AttributeError(msg)
    try:
        result = import_mod(attr_name, _dynamic_imports[attr_name], __spec__.parent)
    except (ModuleNotFoundError, ImportError, AttributeError) as e:
        msg = f"Could not import '{attr_name}' from '{__name__}': {e}"
        raise AttributeError(msg) from e
    globals()[attr_name] = result
    return result


def __dir__() -> list[str]:
    """暴露模块可用的属性列表。

    契约：输入无；输出 `__all__` 的列表副本；副作用无；失败语义：无。
    关键路径：1) 返回 `__all__`。
    决策：以 `__all__` 作为唯一来源
    问题：保持导出项与文档一致
    方案：集中维护 `__all__`
    代价：新增组件需同步更新列表
    重评：当引入自动注册机制时由注册表生成
    """
    return list(__all__)
