"""
模块名称：Elasticsearch 向量检索组件

本模块提供 Elasticsearch 向量存储与检索组件，支持连接配置、文档写入与相似度搜索。
主要功能包括：
- 生成并配置 Elasticsearch 向量存储实例
- 将输入数据转换为 LangChain 文档并写入索引
- 支持相似度搜索与 MMR 检索

关键组件：
- `ElasticsearchVectorStoreComponent`：Elasticsearch 向量存储组件入口

设计背景：为基于 Elasticsearch 的向量检索提供统一组件封装。
注意事项：Cloud 与本地部署参数不可同时使用，配置冲突会抛 `ValueError`。
"""

from typing import Any

from elasticsearch import Elasticsearch
from langchain_core.documents import Document
from langchain_elasticsearch import ElasticsearchStore

from lfx.base.vectorstores.model import LCVectorStoreComponent, check_cached_vector_store
from lfx.io import (
    BoolInput,
    DropdownInput,
    FloatInput,
    HandleInput,
    IntInput,
    SecretStrInput,
    StrInput,
)
from lfx.schema.data import Data


class ElasticsearchVectorStoreComponent(LCVectorStoreComponent):
    """Elasticsearch 向量存储组件。

    契约：输入连接配置、索引名与嵌入器，输出可用的向量存储实例与检索结果。
    副作用：可能创建索引、写入文档并发起网络请求。
    失败语义：配置冲突或连接失败会抛 `ValueError`。
    """

    display_name: str = "Elasticsearch"
    description: str = "Elasticsearch Vector Store with with advanced, customizable search capabilities."
    name = "Elasticsearch"
    icon = "ElasticsearchStore"

    inputs = [
        StrInput(
            name="elasticsearch_url",
            display_name="Elasticsearch URL",
            value="http://localhost:9200",
            info="URL for self-managed Elasticsearch deployments (e.g., http://localhost:9200). "
            "Do not use with Elastic Cloud deployments, use Elastic Cloud ID instead.",
        ),
        SecretStrInput(
            name="cloud_id",
            display_name="Elastic Cloud ID",
            value="",
            info="Use this for Elastic Cloud deployments. Do not use together with 'Elasticsearch URL'.",
        ),
        StrInput(
            name="index_name",
            display_name="Index Name",
            value="langflow",
            info="The index name where the vectors will be stored in Elasticsearch cluster.",
        ),
        *LCVectorStoreComponent.inputs,
        StrInput(
            name="username",
            display_name="Username",
            value="",
            advanced=False,
            info=(
                "Elasticsearch username (e.g., 'elastic'). "
                "Required for both local and Elastic Cloud setups unless API keys are used."
            ),
        ),
        SecretStrInput(
            name="password",
            display_name="Elasticsearch Password",
            value="",
            advanced=False,
            info=(
                "Elasticsearch password for the specified user. "
                "Required for both local and Elastic Cloud setups unless API keys are used."
            ),
        ),
        HandleInput(
            name="embedding",
            display_name="Embedding",
            input_types=["Embeddings"],
        ),
        DropdownInput(
            name="search_type",
            display_name="Search Type",
            options=["similarity", "mmr"],
            value="similarity",
            advanced=True,
        ),
        IntInput(
            name="number_of_results",
            display_name="Number of Results",
            info="Number of results to return.",
            advanced=True,
            value=4,
        ),
        FloatInput(
            name="search_score_threshold",
            display_name="Search Score Threshold",
            info="Minimum similarity score threshold for search results.",
            value=0.0,
            advanced=True,
        ),
        SecretStrInput(
            name="api_key",
            display_name="Elastic API Key",
            value="",
            advanced=True,
            info="API Key for Elastic Cloud authentication. If used, 'username' and 'password' are not required.",
        ),
        BoolInput(
            name="verify_certs",
            display_name="Verify SSL Certificates",
            value=True,
            advanced=True,
            info="Whether to verify SSL certificates when connecting to Elasticsearch.",
        ),
    ]

    @check_cached_vector_store
    def build_vector_store(self) -> ElasticsearchStore:
        """构建 Elasticsearch 向量存储实例。

        契约：输入为连接参数与嵌入器配置，输出 `ElasticsearchStore`。
        关键路径（三步）：
        1) 校验 Cloud/本地连接参数互斥关系。
        2) 组装连接参数并构建客户端（含 SSL 验证选项）。
        3) 初始化向量存储并按需写入文档。

        异常流：参数冲突抛 `ValueError`。
        """
        if self.cloud_id and self.elasticsearch_url:
            msg = (
                "Both 'cloud_id' and 'elasticsearch_url' provided. "
                "Please use only one based on your deployment (Cloud or Local)."
            )
            raise ValueError(msg)

        es_params = {
            "index_name": self.index_name,
            "embedding": self.embedding,
            "es_user": self.username or None,
            "es_password": self.password or None,
        }

        if self.cloud_id:
            es_params["es_cloud_id"] = self.cloud_id
        else:
            es_params["es_url"] = self.elasticsearch_url

        if self.api_key:
            es_params["api_key"] = self.api_key

        if self.verify_certs is False:
            client_params: dict[str, Any] = {}
            client_params["verify_certs"] = False

            if self.cloud_id:
                client_params["cloud_id"] = self.cloud_id
            else:
                client_params["hosts"] = [self.elasticsearch_url]

            if self.api_key:
                client_params["api_key"] = self.api_key
            elif self.username and self.password:
                client_params["basic_auth"] = (self.username, self.password)

            es_client = Elasticsearch(**client_params)
            es_params["es_connection"] = es_client

        elasticsearch = ElasticsearchStore(**es_params)

        if self.ingest_data:
            documents = self._prepare_documents()
            if documents:
                elasticsearch.add_documents(documents)

        return elasticsearch

    def _prepare_documents(self) -> list[Document]:
        """准备写入向量存储的文档列表。

        契约：输入为 `ingest_data`，输出 `Document` 列表。
        副作用：调用父类数据预处理逻辑。
        失败语义：输入非 `Data` 时抛 `TypeError`。
        """
        self.ingest_data = self._prepare_ingest_data()

        documents = []
        for data in self.ingest_data:
            if isinstance(data, Data):
                documents.append(data.to_lc_document())
            else:
                error_message = "Vector Store Inputs must be Data objects."
                self.log(error_message)
                raise TypeError(error_message)
        return documents

    def _add_documents_to_vector_store(self, vector_store: "ElasticsearchStore") -> None:
        """向向量存储写入文档。

        契约：输入向量存储实例，写入当前 `ingest_data`。
        副作用：执行文档写入操作并记录日志。
        失败语义：无文档时仅记录日志。
        """
        documents = self._prepare_documents()
        if documents and self.embedding:
            self.log(f"Adding {len(documents)} documents to the Vector Store.")
            vector_store.add_documents(documents)
        else:
            self.log("No documents to add to the Vector Store.")

    def search(self, query: str | None = None) -> list[dict[str, Any]]:
        """在向量存储中执行检索。

        契约：输入查询文本（可选），输出结果列表（含内容/元数据/分数）。
        关键路径（三步）：
        1) 初始化向量存储与检索参数。
        2) 根据 `search_type` 执行相似度或 MMR 检索。
        3) 无查询时返回索引内文档。

        异常流：检索类型非法抛 `ValueError`。
        """
        vector_store = self.build_vector_store()
        search_kwargs = {
            "k": self.number_of_results,
            "score_threshold": self.search_score_threshold,
        }

        if query:
            search_type = self.search_type.lower()
            if search_type not in {"similarity", "mmr"}:
                msg = f"Invalid search type: {self.search_type}"
                self.log(msg)
                raise ValueError(msg)
            try:
                if search_type == "similarity":
                    results = vector_store.similarity_search_with_score(query, **search_kwargs)
                elif search_type == "mmr":
                    results = vector_store.max_marginal_relevance_search(query, **search_kwargs)
            except Exception as e:
                msg = (
                    "Error occurred while querying the Elasticsearch VectorStore,"
                    " there is no Data into the VectorStore."
                )
                self.log(msg)
                raise ValueError(msg) from e
            return [
                {"page_content": doc.page_content, "metadata": doc.metadata, "score": score} for doc, score in results
            ]
        results = self.get_all_documents(vector_store, **search_kwargs)
        return [{"page_content": doc.page_content, "metadata": doc.metadata, "score": score} for doc, score in results]

    def get_all_documents(self, vector_store: ElasticsearchStore, **kwargs) -> list[tuple[Document, float]]:
        """获取索引中的文档列表。

        契约：输入向量存储与限制参数，输出 `(Document, score)` 列表。
        副作用：发起 Elasticsearch 查询请求。
        失败语义：异常由底层客户端抛出。
        """
        client = vector_store.client
        index_name = self.index_name

        query = {
            "query": {"match_all": {}},
            "size": kwargs.get("k", self.number_of_results),
        }

        response = client.search(index=index_name, body=query)

        results = []
        for hit in response["hits"]["hits"]:
            doc = Document(
                page_content=hit["_source"].get("text", ""),
                metadata=hit["_source"].get("metadata", {}),
            )
            score = hit["_score"]
            results.append((doc, score))

        return results

    def search_documents(self) -> list[Data]:
        """执行检索并返回 `Data` 列表。

        契约：使用 `search_query` 执行检索并输出 `Data`。
        副作用：更新 `self.status`。
        失败语义：异常由 `search` 抛出。
        """
        results = self.search(self.search_query)
        retrieved_data = [
            Data(
                text=result["page_content"],
                file_path=result["metadata"].get("file_path", ""),
            )
            for result in results
        ]
        self.status = retrieved_data
        return retrieved_data

    def get_retriever_kwargs(self):
        """返回检索器参数。

        契约：输出包含 `search_type` 与 `search_kwargs` 的字典。
        副作用：无。
        失败语义：无。
        """
        return {
            "search_type": self.search_type.lower(),
            "search_kwargs": {
                "k": self.number_of_results,
                "score_threshold": self.search_score_threshold,
            },
        }
