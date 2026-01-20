"""模块名称：初始项目加载入口

模块目的：提供 `starter projects` 的统一加载与导出入口。
主要功能：构建 `starter projects` 的图对象列表并导出为可序列化数据。
使用场景：初始化数据库或导出示例项目数据。
关键组件：`get_starter_projects_graphs`、`get_starter_projects_dump`
设计背景：将示例项目构建逻辑与调用处解耦。
注意事项：返回顺序影响前端展示，请保持稳定。
"""

from .starter_projects import (
    basic_prompting_graph,
    blog_writer_graph,
    document_qa_graph,
    memory_chatbot_graph,
    vector_store_rag_graph,
)


def get_starter_projects_graphs():
    """构建并返回 `starter projects` 的图对象列表。

    契约：按固定顺序返回图对象，调用方不应修改返回列表。
    """
    return [
        basic_prompting_graph(),
        blog_writer_graph(),
        document_qa_graph(),
        memory_chatbot_graph(),
        vector_store_rag_graph(),
    ]


def get_starter_projects_dump():
    """返回 `starter projects` 的序列化数据列表。"""
    return [g.dump() for g in get_starter_projects_graphs()]
