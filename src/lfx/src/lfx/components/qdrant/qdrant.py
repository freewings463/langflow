"""
模块名称：Qdrant 向量库组件

本模块提供 Qdrant 向量检索组件，支持本地/远程服务连接与相似度检索。
主要功能包括：
- 组装 Qdrant 连接参数并构建向量库实例
- 将输入文档写入集合或连接已有集合
- 执行相似度检索并返回 `Data` 列表

关键组件：
- `QdrantVectorStoreComponent`
- `build_vector_store`
- `search_documents`

设计背景：以统一接口接入 Qdrant 向量库能力。
注意事项：`embedding` 必须为 `Embeddings` 实例；未提供文档时仅建立客户端连接。
"""

from langchain_community.vectorstores import Qdrant
from langchain_core.embeddings import Embeddings

from lfx.base.vectorstores.model import LCVectorStoreComponent, check_cached_vector_store
from lfx.helpers.data import docs_to_data
from lfx.io import (
    DropdownInput,
    HandleInput,
    IntInput,
    SecretStrInput,
    StrInput,
)
from lfx.schema.data import Data


class QdrantVectorStoreComponent(LCVectorStoreComponent):
    """Qdrant 向量库组件。

    契约：
    - 输入：集合名、连接参数、距离函数与向量字段配置
    - 输出：向量库实例或检索结果 `list[Data]`
    - 副作用：可能创建集合并写入文档
    - 失败语义：`embedding` 类型错误时抛 `TypeError`
    """

    display_name = "Qdrant"
    description = "Qdrant Vector Store with search capabilities"
    icon = "Qdrant"

    inputs = [
        StrInput(name="collection_name", display_name="Collection Name", required=True),
        StrInput(name="host", display_name="Host", value="localhost", advanced=True),
        IntInput(name="port", display_name="Port", value=6333, advanced=True),
        IntInput(name="grpc_port", display_name="gRPC Port", value=6334, advanced=True),
        SecretStrInput(name="api_key", display_name="Qdrant API Key", advanced=True),
        StrInput(name="prefix", display_name="Prefix", advanced=True),
        IntInput(name="timeout", display_name="Timeout", advanced=True),
        StrInput(name="path", display_name="Path", advanced=True),
        StrInput(name="url", display_name="URL", advanced=True),
        DropdownInput(
            name="distance_func",
            display_name="Distance Function",
            options=["Cosine", "Euclidean", "Dot Product"],
            value="Cosine",
            advanced=True,
        ),
        StrInput(name="content_payload_key", display_name="Content Payload Key", value="page_content", advanced=True),
        StrInput(name="metadata_payload_key", display_name="Metadata Payload Key", value="metadata", advanced=True),
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
    def build_vector_store(self) -> Qdrant:
        """构建 Qdrant 向量库实例。

        关键路径（三步）：
        1) 组装集合与服务端配置参数
        2) 规范化输入文档并校验 `embedding` 类型
        3) 有文档则写入创建；无文档则仅连接现有集合

        异常流：`embedding` 不是 `Embeddings` 时抛 `TypeError`。
        """
        qdrant_kwargs = {
            "collection_name": self.collection_name,
            "content_payload_key": self.content_payload_key,
            "metadata_payload_key": self.metadata_payload_key,
        }

        server_kwargs = {
            "host": self.host or None,
            "port": int(self.port),  # Ensure port is an integer
            "grpc_port": int(self.grpc_port),  # Ensure grpc_port is an integer
            "api_key": self.api_key,
            "prefix": self.prefix,
            # Ensure timeout is an integer
            "timeout": int(self.timeout) if self.timeout else None,
            "path": self.path or None,
            "url": self.url or None,
        }

        server_kwargs = {k: v for k, v in server_kwargs.items() if v is not None}

        # 注意：输入数据可能来自 DataFrame/自定义类型，先统一为 LangChain 文档
        self.ingest_data = self._prepare_ingest_data()

        documents = []
        for _input in self.ingest_data or []:
            if isinstance(_input, Data):
                documents.append(_input.to_lc_document())
            else:
                documents.append(_input)

        if not isinstance(self.embedding, Embeddings):
            msg = "Invalid embedding object"
            raise TypeError(msg)

        if documents:
            qdrant = Qdrant.from_documents(documents, embedding=self.embedding, **qdrant_kwargs, **server_kwargs)
        else:
            from qdrant_client import QdrantClient

            client = QdrantClient(**server_kwargs)
            qdrant = Qdrant(embeddings=self.embedding, client=client, **qdrant_kwargs)

        return qdrant

    def search_documents(self) -> list[Data]:
        """执行向量检索并返回 `Data` 列表。

        契约：
        - 输入：`search_query` 文本与 `number_of_results`
        - 输出：`list[Data]`；空查询返回空列表
        - 副作用：更新 `self.status`
        - 失败语义：底层向量库异常将向上抛出
        """
        vector_store = self.build_vector_store()

        if self.search_query and isinstance(self.search_query, str) and self.search_query.strip():
            docs = vector_store.similarity_search(
                query=self.search_query,
                k=self.number_of_results,
            )

            data = docs_to_data(docs)
            self.status = data
            return data
        return []
