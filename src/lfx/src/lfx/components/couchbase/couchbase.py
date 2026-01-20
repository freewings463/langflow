"""
模块名称：Couchbase 向量检索组件

本模块提供基于 Couchbase 的向量存储组件，主要用于构建向量索引并执行相似度检索。主要功能包括：
- 连接 Couchbase 集群并初始化向量存储
- 可选地将输入文档写入集合
- 执行相似度搜索并返回 `Data` 结果

关键组件：
- `CouchbaseVectorStoreComponent`：向量存储组件实现

设计背景：在 Langflow 中接入 Couchbase 向量检索能力，复用 LangChain 向量存储接口。
注意事项：该组件依赖 `langflow[couchbase]` 额外依赖包；连接失败会抛异常。
"""

from datetime import timedelta

from langchain_community.vectorstores import CouchbaseVectorStore

from lfx.base.vectorstores.model import LCVectorStoreComponent, check_cached_vector_store
from lfx.helpers.data import docs_to_data
from lfx.io import HandleInput, IntInput, SecretStrInput, StrInput
from lfx.schema.data import Data


class CouchbaseVectorStoreComponent(LCVectorStoreComponent):
    """Couchbase 向量存储组件。

    契约：需要提供连接串、用户名/密码、bucket/scope/collection 与索引名。
    失败语义：缺少依赖或连接失败时抛 `ImportError`/`ValueError`。
    副作用：可能创建索引并向 Couchbase 写入文档。
    """

    display_name = "Couchbase"
    description = "Couchbase Vector Store with search capabilities"
    name = "Couchbase"
    icon = "Couchbase"

    inputs = [
        SecretStrInput(
            name="couchbase_connection_string", display_name="Couchbase Cluster connection string", required=True
        ),
        StrInput(name="couchbase_username", display_name="Couchbase username", required=True),
        SecretStrInput(name="couchbase_password", display_name="Couchbase password", required=True),
        StrInput(name="bucket_name", display_name="Bucket Name", required=True),
        StrInput(name="scope_name", display_name="Scope Name", required=True),
        StrInput(name="collection_name", display_name="Collection Name", required=True),
        StrInput(name="index_name", display_name="Index Name", required=True),
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
    def build_vector_store(self) -> CouchbaseVectorStore:
        """构建 Couchbase 向量存储实例。

        契约：若存在待写入文档，则调用 `from_documents` 创建并写入；否则仅构造实例。
        失败语义：依赖缺失抛 `ImportError`；连接失败抛 `ValueError`。
        副作用：连接 Couchbase 并可能写入文档/创建索引。

        关键路径（三步）：
        1) 校验依赖并建立集群连接
        2) 准备待写入文档列表
        3) 创建向量存储实例并返回
        """
        try:
            from couchbase.auth import PasswordAuthenticator
            from couchbase.cluster import Cluster
            from couchbase.options import ClusterOptions
        except ImportError as e:
            msg = "Failed to import Couchbase dependencies. Install it using `uv pip install langflow[couchbase] --pre`"
            raise ImportError(msg) from e

        try:
            auth = PasswordAuthenticator(self.couchbase_username, self.couchbase_password)
            options = ClusterOptions(auth)
            cluster = Cluster(self.couchbase_connection_string, options)

            cluster.wait_until_ready(timedelta(seconds=5))
        except Exception as e:
            msg = f"Failed to connect to Couchbase: {e}"
            raise ValueError(msg) from e

        self.ingest_data = self._prepare_ingest_data()

        documents = []
        for _input in self.ingest_data or []:
            if isinstance(_input, Data):
                documents.append(_input.to_lc_document())
            else:
                documents.append(_input)

        if documents:
            couchbase_vs = CouchbaseVectorStore.from_documents(
                documents=documents,
                cluster=cluster,
                bucket_name=self.bucket_name,
                scope_name=self.scope_name,
                collection_name=self.collection_name,
                embedding=self.embedding,
                index_name=self.index_name,
            )

        else:
            couchbase_vs = CouchbaseVectorStore(
                cluster=cluster,
                bucket_name=self.bucket_name,
                scope_name=self.scope_name,
                collection_name=self.collection_name,
                embedding=self.embedding,
                index_name=self.index_name,
            )

        return couchbase_vs

    def search_documents(self) -> list[Data]:
        """执行相似度检索并返回 `Data` 列表。

        契约：仅当 `search_query` 为非空字符串时执行检索。
        失败语义：向量存储初始化失败时抛异常；检索失败由底层抛出。
        副作用：可能更新组件 `status`。
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
