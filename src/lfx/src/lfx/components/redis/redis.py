from pathlib import Path

from langchain_community.vectorstores.redis import Redis
from langchain_text_splitters import CharacterTextSplitter

from lfx.base.vectorstores.model import LCVectorStoreComponent, check_cached_vector_store
from lfx.helpers.data import docs_to_data
from lfx.io import HandleInput, IntInput, SecretStrInput, StrInput
from lfx.schema.data import Data


class RedisVectorStoreComponent(LCVectorStoreComponent):
    """A custom component for implementing a Vector Store using Redis."""

    # Redis 向量存储组件配置
    display_name: str = "Redis"
    description: str = "Implementation of Vector Store using Redis"
    name = "Redis"
    icon = "Redis"

    inputs = [
        SecretStrInput(name="redis_server_url", display_name="Redis Server Connection String", required=True),
        StrInput(
            name="redis_index_name",
            display_name="Redis Index",
        ),
        StrInput(name="code", display_name="Code", advanced=True),
        StrInput(
            name="schema",
            display_name="Schema",
        ),
        *LCVectorStoreComponent.inputs,
        IntInput(
            name="number_of_results",
            display_name="Number of Results",
            info="Number of results to return.",
            value=4,
            advanced=True,
        ),
        HandleInput(name="embedding", display_name="Embedding", input_types=["Embeddings"]),
    ]

    @check_cached_vector_store
    def build_vector_store(self) -> Redis:
        # 使用父类方法准备待导入数据
        self.ingest_data = self._prepare_ingest_data()

        documents = []
        for _input in self.ingest_data or []:
            if isinstance(_input, Data):
                documents.append(_input.to_lc_document())
            else:
                documents.append(_input)
        # 本地调试输出（保留现有行为）
        Path("docuemnts.txt").write_text(str(documents), encoding="utf-8")

        if not documents:
            if self.schema is None:
                # 无文档时必须提供 schema
                msg = "If no documents are provided, a schema must be provided."
                raise ValueError(msg)
            # 连接已有索引
            redis_vs = Redis.from_existing_index(
                embedding=self.embedding,
                index_name=self.redis_index_name,
                schema=self.schema,
                key_prefix=None,
                redis_url=self.redis_server_url,
            )
        else:
            # 有文档时先切分再创建索引
            text_splitter = CharacterTextSplitter(chunk_size=1000, chunk_overlap=0)
            docs = text_splitter.split_documents(documents)
            redis_vs = Redis.from_documents(
                documents=docs,
                embedding=self.embedding,
                redis_url=self.redis_server_url,
                index_name=self.redis_index_name,
            )
        return redis_vs

    def search_documents(self) -> list[Data]:
        # 构建向量存储并执行相似度检索
        vector_store = self.build_vector_store()

        if self.search_query and isinstance(self.search_query, str) and self.search_query.strip():
            docs = vector_store.similarity_search(
                query=self.search_query,
                k=self.number_of_results,
            )

            # 将文档转换为 Data 返回
            data = docs_to_data(docs)
            self.status = data
            return data
        return []
