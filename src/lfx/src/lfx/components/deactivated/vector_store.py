"""
模块名称：VectorStore Retriever 组件（已停用）

本模块提供将 `VectorStore` 转换为 `VectorStoreRetriever` 的能力，主要用于旧流程中快速生成检索器。主要功能包括：
- 调用 `vectorstore.as_retriever()` 构建检索器

关键组件：
- `VectorStoreRetrieverComponent`：检索器组件

设计背景：早期流程中需要显式从向量存储得到检索器。
注意事项：输入必须实现 `as_retriever` 接口。
"""

from langchain_core.vectorstores import VectorStoreRetriever

from lfx.custom.custom_component.custom_component import CustomComponent
from lfx.field_typing import VectorStore
from lfx.inputs.inputs import HandleInput


class VectorStoreRetrieverComponent(CustomComponent):
    """向量存储检索器组件。

    契约：输入 `VectorStore`，输出 `VectorStoreRetriever`。
    失败语义：`as_retriever` 不存在或失败时抛异常。
    副作用：无。
    """
    display_name = "VectorStore Retriever"
    description = "A vector store retriever"
    name = "VectorStoreRetriever"
    icon = "LangChain"

    inputs = [
        HandleInput(
            name="vectorstore",
            display_name="Vector Store",
            input_types=["VectorStore"],
            required=True,
        ),
    ]

    def build(self, vectorstore: VectorStore) -> VectorStoreRetriever:
        """构建检索器。

        契约：调用 `vectorstore.as_retriever()` 并返回。
        失败语义：底层抛异常则向上抛出。
        副作用：无。
        """
        return vectorstore.as_retriever()
