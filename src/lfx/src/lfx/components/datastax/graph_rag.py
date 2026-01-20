"""
模块名称：Graph RAG 检索组件

本模块提供基于图检索策略的 RAG 组件封装，通过 GraphRetriever 执行图遍历。主要功能包括：
- 枚举可用遍历策略并执行检索
- 解析边定义并构造图边配置

关键组件：
- `GraphRAGComponent`

设计背景：需要将图结构检索能力接入 LFX 流程。
使用场景：基于图关系的检索增强生成。
注意事项：依赖 `graph_retriever` 与 `langchain_graph_retriever`。
"""

import inspect
from abc import ABC

import graph_retriever.strategies as strategies_module
from langchain_graph_retriever import GraphRetriever

from lfx.base.vectorstores.model import LCVectorStoreComponent
from lfx.helpers.data import docs_to_data
from lfx.inputs.inputs import DropdownInput, HandleInput, MultilineInput, NestedDictInput, StrInput
from lfx.schema.data import Data


def traversal_strategies() -> list[str]:
    """获取可用的图遍历策略名称列表

    契约：返回策略类名列表；副作用：无；失败语义：无。
    关键路径：从 `strategies_module` 中筛选非抽象策略类。
    决策：排除继承 `ABC` 的基类。
    问题：策略模块包含基类与实现类混合。
    方案：过滤 `ABC` 基类。
    代价：若策略继承层级变化可能需调整过滤条件。
    重评：当策略注册机制替代反射时。
    """
    classes = inspect.getmembers(strategies_module, inspect.isclass)
    return [name for name, cls in classes if ABC not in cls.__bases__]


class GraphRAGComponent(LCVectorStoreComponent):
    """Graph RAG 组件

    契约：输入向量库连接、策略与边定义；输出 `list[Data]`；
    副作用：调用图检索并更新 `self.status`；
    失败语义：策略/边定义错误或检索异常透传。
    关键路径：1) 解析边定义 2) 构建 GraphRetriever 3) 执行检索并转换为 `Data`。
    决策：将边定义作为字符串解析以兼容前端输入。
    问题：前端配置需要简单文本形式。
    方案：用逗号分隔并支持 `Id()` 特殊语义。
    代价：解析错误时难以定位具体字段。
    重评：当引入结构化边定义输入时。
    """

    display_name: str = "Graph RAG"
    description: str = "Graph RAG traversal for vector store."
    name = "GraphRAG"
    icon: str = "AstraDB"

    inputs = [
        HandleInput(
            name="embedding_model",
            display_name="Embedding Model",
            input_types=["Embeddings"],
            info="Specify the Embedding Model. Not required for Astra Vectorize collections.",
            required=False,
        ),
        HandleInput(
            name="vector_store",
            display_name="Vector Store Connection",
            input_types=["VectorStore"],
            info="Connection to Vector Store.",
        ),
        StrInput(
            name="edge_definition",
            display_name="Edge Definition",
            info="Edge definition for the graph traversal.",
        ),
        DropdownInput(
            name="strategy",
            display_name="Traversal Strategies",
            options=traversal_strategies(),
        ),
        MultilineInput(
            name="search_query",
            display_name="Search Query",
            tool_mode=True,
        ),
        NestedDictInput(
            name="graphrag_strategy_kwargs",
            display_name="Strategy Parameters",
            info=(
                "Optional dictionary of additional parameters for the retrieval strategy. "
                "Please see https://datastax.github.io/graph-rag/reference/graph_retriever/strategies/ for details."
            ),
            advanced=True,
        ),
    ]

    def search_documents(self) -> list[Data]:
        """执行图检索并返回 `Data` 列表

        契约：使用 `vector_store`/`strategy`/`edge_definition` 执行检索；
        副作用：调用 GraphRetriever；
        失败语义：策略类不存在或调用异常透传。
        关键路径：1) 解析策略与边定义 2) 构建 retriever 3) 调用 `invoke`。
        """
        additional_params = self.graphrag_strategy_kwargs or {}

        strategy_class = getattr(strategies_module, self.strategy)
        retriever = GraphRetriever(
            store=self.vector_store,
            edges=[self._evaluate_edge_definition_input()],
            strategy=strategy_class(**additional_params),
        )

        return docs_to_data(retriever.invoke(self.search_query))

    def _edge_definition_from_input(self) -> tuple:
        """解析边定义输入

        契约：将逗号分隔字符串转换为元组；副作用：无；失败语义：无。
        """
        values = self.edge_definition.split(",")
        values = [value.strip() for value in values]

        return tuple(values)

    def _evaluate_edge_definition_input(self) -> tuple:
        """解析边定义中的特殊函数

        契约：返回解析后的边定义元组；副作用：无；
        失败语义：未知标记会原样保留。
        关键路径：识别 `Id()` 并转换为对象实例。
        """
        from graph_retriever.edges.metadata import Id

        evaluated_values = []
        for value in self._edge_definition_from_input():
            if value == "Id()":
                evaluated_values.append(Id())
            else:
                evaluated_values.append(value)
        return tuple(evaluated_values)
