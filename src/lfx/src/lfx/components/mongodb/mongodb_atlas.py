"""
模块名称：MongoDB Atlas 向量库组件

本模块提供 MongoDB Atlas Vector Search 的向量库组件，支持写入文档与相似度检索。
主要功能包括：
- 建立 MongoDB Atlas 连接（支持 mTLS）
- 创建/校验向量搜索索引
- 构建 `MongoDBAtlasVectorSearch` 并执行相似度检索

关键组件：
- `MongoVectorStoreComponent`
- `build_vector_store`
- `search_documents`

设计背景：在 Langflow 中以统一接口接入 Atlas 向量检索。
注意事项：mTLS 会写入临时证书文件；索引创建后需等待生效。
"""

import tempfile
import time

import certifi
from langchain_community.vectorstores import MongoDBAtlasVectorSearch
from pymongo.collection import Collection
from pymongo.operations import SearchIndexModel

from lfx.base.vectorstores.model import LCVectorStoreComponent, check_cached_vector_store
from lfx.helpers.data import docs_to_data
from lfx.io import BoolInput, DropdownInput, HandleInput, IntInput, SecretStrInput, StrInput
from lfx.schema.data import Data


class MongoVectorStoreComponent(LCVectorStoreComponent):
    """MongoDB Atlas 向量库组件。

    契约：
    - 输入：连接 URI、数据库/集合/索引信息、向量维度与相似度配置
    - 输出：向量库实例或检索结果 `list[Data]`
    - 副作用：可能创建索引、写入文档、删除文档（覆盖模式）
    - 失败语义：连接或索引异常时抛 `ValueError`/`ImportError`
    """

    display_name = "MongoDB Atlas"
    description = "MongoDB Atlas Vector Store with search capabilities"
    name = "MongoDBAtlasVector"
    icon = "MongoDB"
    INSERT_MODES = ["append", "overwrite"]
    SIMILARITY_OPTIONS = ["cosine", "euclidean", "dotProduct"]
    QUANTIZATION_OPTIONS = ["scalar", "binary"]
    inputs = [
        SecretStrInput(name="mongodb_atlas_cluster_uri", display_name="MongoDB Atlas Cluster URI", required=True),
        BoolInput(name="enable_mtls", display_name="Enable mTLS", value=False, advanced=True, required=True),
        SecretStrInput(
            name="mongodb_atlas_client_cert",
            display_name="MongoDB Atlas Combined Client Certificate",
            required=False,
            info="Client Certificate combined with the private key in the following format:\n "
            "-----BEGIN PRIVATE KEY-----\n...\n -----END PRIVATE KEY-----\n-----BEGIN CERTIFICATE-----\n"
            "...\n-----END CERTIFICATE-----\n",
        ),
        StrInput(name="db_name", display_name="Database Name", required=True),
        StrInput(name="collection_name", display_name="Collection Name", required=True),
        StrInput(
            name="index_name",
            display_name="Index Name",
            required=True,
            info="The name of Atlas Search index, it should be a Vector Search.",
        ),
        *LCVectorStoreComponent.inputs,
        DropdownInput(
            name="insert_mode",
            display_name="Insert Mode",
            options=INSERT_MODES,
            value=INSERT_MODES[0],
            info="How to insert new documents into the collection.",
            advanced=True,
        ),
        HandleInput(name="embedding", display_name="Embedding", input_types=["Embeddings"]),
        IntInput(
            name="number_of_results",
            display_name="Number of Results",
            info="Number of results to return.",
            value=4,
            advanced=True,
        ),
        StrInput(
            name="index_field",
            display_name="Index Field",
            advanced=True,
            required=True,
            info="The field to index.",
            value="embedding",
        ),
        StrInput(
            name="filter_field", display_name="Filter Field", advanced=True, info="The field to filter the index."
        ),
        IntInput(
            name="number_dimensions",
            display_name="Number of Dimensions",
            info="Embedding Context Length.",
            value=1536,
            advanced=True,
            required=True,
        ),
        DropdownInput(
            name="similarity",
            display_name="Similarity",
            options=SIMILARITY_OPTIONS,
            value=SIMILARITY_OPTIONS[0],
            info="The method used to measure the similarity between vectors.",
            advanced=True,
        ),
        DropdownInput(
            name="quantization",
            display_name="Quantization",
            options=QUANTIZATION_OPTIONS,
            value=None,
            info="Quantization reduces memory costs converting 32-bit floats to smaller data types",
            advanced=True,
        ),
    ]

    @check_cached_vector_store
    def build_vector_store(self) -> MongoDBAtlasVectorSearch:
        """构建向量库客户端并可选写入文档。

        关键路径（三步）：
        1) 校验依赖并建立 MongoDB 连接（必要时启用 mTLS）
        2) 规范化输入文档并根据插入模式清理集合
        3) 创建 `MongoDBAtlasVectorSearch` 实例并返回

        异常流：连接/证书写入失败抛 `ValueError`；缺依赖抛 `ImportError`。
        排障入口：异常消息包含 `Failed to connect to MongoDB Atlas` 或证书写入错误。
        """
        try:
            from pymongo import MongoClient
        except ImportError as e:
            msg = "Please install pymongo to use MongoDB Atlas Vector Store"
            raise ImportError(msg) from e

        # 注意：mTLS 需要落盘证书，失败时抛出异常并中止构建
        if self.enable_mtls:
            client_cert_path = None
            try:
                client_cert = self.mongodb_atlas_client_cert.replace(" ", "\n")
                client_cert = client_cert.replace("-----BEGIN\nPRIVATE\nKEY-----", "-----BEGIN PRIVATE KEY-----")
                client_cert = client_cert.replace(
                    "-----END\nPRIVATE\nKEY-----\n-----BEGIN\nCERTIFICATE-----",
                    "-----END PRIVATE KEY-----\n-----BEGIN CERTIFICATE-----",
                )
                client_cert = client_cert.replace("-----END\nCERTIFICATE-----", "-----END CERTIFICATE-----")
                with tempfile.NamedTemporaryFile(delete=False) as client_cert_file:
                    client_cert_file.write(client_cert.encode("utf-8"))
                    client_cert_path = client_cert_file.name

            except Exception as e:
                msg = f"Failed to write certificate to temporary file: {e}"
                raise ValueError(msg) from e

        try:
            mongo_client: MongoClient = (
                MongoClient(
                    self.mongodb_atlas_cluster_uri,
                    tls=True,
                    tlsCertificateKeyFile=client_cert_path,
                    tlsCAFile=certifi.where(),
                )
                if self.enable_mtls
                else MongoClient(self.mongodb_atlas_cluster_uri)
            )

            collection = mongo_client[self.db_name][self.collection_name]

        except Exception as e:
            msg = f"Failed to connect to MongoDB Atlas: {e}"
            raise ValueError(msg) from e

        # 注意：输入数据可能来自 DataFrame/自定义类型，先统一为 LangChain 文档
        self.ingest_data = self._prepare_ingest_data()

        documents = []
        for _input in self.ingest_data or []:
            if isinstance(_input, Data):
                documents.append(_input.to_lc_document())
            else:
                documents.append(_input)

        if documents:
            self.__insert_mode(collection)

            return MongoDBAtlasVectorSearch.from_documents(
                documents=documents, embedding=self.embedding, collection=collection, index_name=self.index_name
            )
        return MongoDBAtlasVectorSearch(embedding=self.embedding, collection=collection, index_name=self.index_name)

    def search_documents(self) -> list[Data]:
        """执行向量相似度检索并返回 Data 列表。

        契约：
        - 输入：`search_query` 文本与 `number_of_results`
        - 输出：`list[Data]`；若无查询条件返回空列表
        - 副作用：必要时创建向量索引；更新 `self.status`
        - 失败语义：索引或网络异常向上抛出
        """
        from bson.objectid import ObjectId

        vector_store = self.build_vector_store()

        self.verify_search_index(vector_store._collection)

        if self.search_query and isinstance(self.search_query, str):
            docs = vector_store.similarity_search(
                query=self.search_query,
                k=self.number_of_results,
            )
            for doc in docs:
                doc.metadata = {
                    key: str(value) if isinstance(value, ObjectId) else value for key, value in doc.metadata.items()
                }

            data = docs_to_data(docs)
            self.status = data
            return data
        return []

    def __insert_mode(self, collection: Collection) -> None:
        """根据插入模式决定是否清空集合。"""
        if self.insert_mode == "overwrite":
            collection.delete_many({})  # 注意：清空文档但保留集合结构

    def verify_search_index(self, collection: Collection) -> None:
        """校验向量索引是否存在，不存在则创建。

        契约：
        - 输入：目标集合
        - 输出：无返回值
        - 副作用：可能创建搜索索引并等待 20 秒
        - 失败语义：索引创建失败将抛出 pymongo 异常
        """
        indexes = collection.list_search_indexes()

        index_names_types = {idx["name"]: idx["type"] for idx in indexes}
        index_names = list(index_names_types.keys())
        index_type = index_names_types.get(self.index_name)
        if self.index_name not in index_names and index_type != "vectorSearch":
            collection.create_search_index(self.__create_index_definition())

            time.sleep(20)  # 注意：等待索引可用，避免紧接检索失败

    def __create_index_definition(self) -> SearchIndexModel:
        """生成向量索引定义（可选包含过滤字段）。"""
        fields = [
            {
                "type": "vector",
                "path": self.index_field,
                "numDimensions": self.number_dimensions,
                "similarity": self.similarity,
                "quantization": self.quantization,
            }
        ]
        if self.filter_field:
            fields.append({"type": "filter", "path": self.filter_field})
        return SearchIndexModel(definition={"fields": fields}, name=self.index_name, type="vectorSearch")
