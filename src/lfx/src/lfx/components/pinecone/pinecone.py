"""
模块名称：`Pinecone` 向量库组件

本模块封装 `Pinecone` 向量库的构建与检索，提供统一的向量存储与相似度搜索能力。
主要功能包括：
- 选择距离策略并初始化 `Pinecone` 索引
- 可选写入文档并进行相似度检索
- 使用 `Float32Embeddings` 统一向量精度

关键组件：
- `PineconeVectorStoreComponent.build_vector_store`：构建/写入向量库
- `PineconeVectorStoreComponent.search_documents`：相似度检索输出
- `Float32Embeddings`：向量精度适配

设计背景：对接托管向量库，保证检索流程一致。
注意事项：`langchain-pinecone` 未安装会直接失败，需要显式依赖。
"""

import numpy as np
from langchain_core.vectorstores import VectorStore

from lfx.base.vectorstores.model import LCVectorStoreComponent, check_cached_vector_store
from lfx.helpers.data import docs_to_data
from lfx.io import DropdownInput, HandleInput, IntInput, SecretStrInput, StrInput
from lfx.schema.data import Data


class PineconeVectorStoreComponent(LCVectorStoreComponent):
    """`Pinecone` 向量库组件入口。

    契约：输入索引名、`embedding` 与 API Key，输出可查询的向量库实例。
    决策：用 `Float32Embeddings` 包装嵌入以保证向量精度一致。
    问题：不同嵌入实现可能输出非 `float32`，影响存储或检索。
    方案：统一转为 `float32` 后传入向量库。
    代价：额外一次类型转换开销。
    重评：当上游嵌入统一输出 `float32` 时可移除包装。
    """
    display_name = "Pinecone"
    description = "Pinecone Vector Store with search capabilities"
    name = "Pinecone"
    icon = "Pinecone"
    inputs = [
        StrInput(name="index_name", display_name="Index Name", required=True),
        StrInput(name="namespace", display_name="Namespace", info="Namespace for the index."),
        DropdownInput(
            name="distance_strategy",
            display_name="Distance Strategy",
            options=["Cosine", "Euclidean", "Dot Product"],
            value="Cosine",
            advanced=True,
        ),
        SecretStrInput(name="pinecone_api_key", display_name="Pinecone API Key", required=True),
        StrInput(
            name="text_key",
            display_name="Text Key",
            info="Key in the record to use as text.",
            value="text",
            advanced=True,
        ),
        *LCVectorStoreComponent.inputs,
        HandleInput(name="embedding", display_name="Embedding", input_types=["Embeddings"]),
        IntInput(
            name="number_of_results",
            display_name="Number of Results",
            info="Number of results to return.",
            value=4,
            advanced=True,
        ),
    ]

    @check_cached_vector_store
    def build_vector_store(self) -> VectorStore:
        """构建并返回 `Pinecone` 向量库实例。

        关键路径（三步）：
        1) 校验依赖并准备距离策略
        2) 初始化向量库实例
        3) 可选写入文档
        异常流：依赖缺失或初始化失败抛 `ValueError`。
        """
        try:
            from langchain_pinecone import PineconeVectorStore
        except ImportError as e:
            msg = "langchain-pinecone is not installed. Please install it with `pip install langchain-pinecone`."
            raise ValueError(msg) from e

        try:
            from langchain_pinecone._utilities import DistanceStrategy

            # 决策：包装嵌入，确保输出 `float32`。
            wrapped_embeddings = Float32Embeddings(self.embedding)

            # 实现：将距离策略映射为枚举值。
            distance_strategy = self.distance_strategy.replace(" ", "_").upper()
            distance_strategy = DistanceStrategy[distance_strategy]

            # 实现：初始化向量库实例。
            pinecone = PineconeVectorStore(
                index_name=self.index_name,
                embedding=wrapped_embeddings,  # Use wrapped embeddings
                text_key=self.text_key,
                namespace=self.namespace,
                distance_strategy=distance_strategy,
                pinecone_api_key=self.pinecone_api_key,
            )
        except Exception as e:
            error_msg = "Error building Pinecone vector store"
            raise ValueError(error_msg) from e
        else:
            self.ingest_data = self._prepare_ingest_data()

            # 实现：将输入转为 `Document` 后写入索引。
            documents = []
            if self.ingest_data:
                for doc in self.ingest_data:
                    if isinstance(doc, Data):
                        documents.append(doc.to_lc_document())
                    else:
                        documents.append(doc)

                if documents:
                    pinecone.add_documents(documents)

            return pinecone

    def search_documents(self) -> list[Data]:
        """执行相似度检索并返回 `Data` 列表。

        契约：当 `search_query` 为空或仅空白时返回空列表。
        排障入口：`status` 会写入检索结果，便于前端展示。
        """
        try:
            if not self.search_query or not isinstance(self.search_query, str) or not self.search_query.strip():
                return []

            vector_store = self.build_vector_store()
            docs = vector_store.similarity_search(
                query=self.search_query,
                k=self.number_of_results,
            )
        except Exception as e:
            error_msg = "Error searching documents"
            raise ValueError(error_msg) from e
        else:
            data = docs_to_data(docs)
            self.status = data
            return data


class Float32Embeddings:
    """将嵌入输出统一为 `float32` 的包装器。"""

    def __init__(self, base_embeddings):
        self.base_embeddings = base_embeddings

    def embed_documents(self, texts):
        """批量文本嵌入，确保返回 `float32`。"""
        embeddings = self.base_embeddings.embed_documents(texts)
        if isinstance(embeddings, np.ndarray):
            return [[self._force_float32(x) for x in vec] for vec in embeddings]
        return [[self._force_float32(x) for x in vec] for vec in embeddings]

    def embed_query(self, text):
        """单条查询嵌入，确保返回 `float32`。"""
        embedding = self.base_embeddings.embed_query(text)
        if isinstance(embedding, np.ndarray):
            return [self._force_float32(x) for x in embedding]
        return [self._force_float32(x) for x in embedding]

    def _force_float32(self, value):
        """将任意数值类型强制转换为 `float`。"""
        return float(np.float32(value))
