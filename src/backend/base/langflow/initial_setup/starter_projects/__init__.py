"""
模块名称：`Starter Projects` 图构建入口

本模块集中导出启动示例的图构建函数，供初始化流程装载默认模板。主要功能包括：
- 暴露多个示例 `Graph` 构建器（提示、Agent、RAG 等）
- 统一 `__all__` 以保持对外导出稳定

关键组件：
- `basic_prompting_graph` / `blog_writer_graph` / `document_qa_graph`
- `complex_agent_graph` / `hierarchical_tasks_agent_graph` / `sequential_tasks_agent_graph`
- `memory_chatbot_graph` / `vector_store_rag_graph`

设计背景：新用户需要可运行的示例图快速理解产品能力。
注意事项：此模块仅做导出聚合，不应引入运行时副作用。
"""

from .basic_prompting import basic_prompting_graph
from .blog_writer import blog_writer_graph
from .complex_agent import complex_agent_graph
from .document_qa import document_qa_graph
from .hierarchical_tasks_agent import hierarchical_tasks_agent_graph
from .memory_chatbot import memory_chatbot_graph
from .sequential_tasks_agent import sequential_tasks_agent_graph
from .vector_store_rag import vector_store_rag_graph

# 注意：显式导出用于稳定对外 `API`，新增示例需同步更新。
__all__ = [
    "basic_prompting_graph",
    "blog_writer_graph",
    "complex_agent_graph",
    "document_qa_graph",
    "hierarchical_tasks_agent_graph",
    "memory_chatbot_graph",
    "sequential_tasks_agent_graph",
    "vector_store_rag_graph",
]
