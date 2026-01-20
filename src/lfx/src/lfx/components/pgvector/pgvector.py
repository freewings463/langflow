"""
模块名称：`PGVector` 向量库组件

本模块封装 `PGVector` 向量库的构建与检索，提供统一的向量存储与相似度搜索能力。
主要功能包括：
- 将输入数据转换为 `PGVector` 可写入的 `Document` 列表
- 使用连接串创建或复用集合索引
- 基于查询文本进行相似度检索并转为 `Data`

关键组件：
- `PGVectorStoreComponent.build_vector_store`：构建/复用向量库
- `PGVectorStoreComponent.search_documents`：相似度搜索输出

设计背景：为低代码流程提供可复用的 `PostgreSQL` 向量存储组件。
注意事项：连接失败或权限不足会在构建阶段抛出异常，调用方需处理。
"""

from langchain_community.vectorstores import PGVector

from lfx.base.vectorstores.model import LCVectorStoreComponent, check_cached_vector_store
from lfx.helpers.data import docs_to_data
from lfx.io import HandleInput, IntInput, SecretStrInput, StrInput
from lfx.schema.data import Data
from lfx.utils.connection_string_parser import transform_connection_string


class PGVectorStoreComponent(LCVectorStoreComponent):
    """`PGVector` 向量库组件入口。

    契约：输入连接串、集合名与 `embedding`，输出可查询的向量库实例。
    决策：有文档时调用 `from_documents`，无文档则复用已有索引。
    问题：初始化阶段既要支持新建写入，也要支持直接查询。
    方案：根据 `documents` 是否为空选择构建路径。
    代价：集合不存在且无文档时会触发构建失败。
    重评：当需要强制建表或自动建库时扩展初始化策略。
    """
    display_name = "PGVector"
    description = "PGVector Vector Store with search capabilities"
    name = "pgvector"
    icon = "cpu"

    inputs = [
        SecretStrInput(name="pg_server_url", display_name="PostgreSQL Server Connection String", required=True),
        StrInput(name="collection_name", display_name="Table", required=True),
        *LCVectorStoreComponent.inputs,
        HandleInput(name="embedding", display_name="Embedding", input_types=["Embeddings"], required=True),
        IntInput(
            name="number_of_results",
            display_name="Number of Results",
            info="Number of results to return.",
            value=4,
            advanced=True,
        ),
    ]

    @check_cached_vector_store
    def build_vector_store(self) -> PGVector:
        """构建或复用 `PGVector` 向量库实例。

        关键路径（三步）：
        1) 统一 `ingest_data` 类型
        2) 解析连接串并准备 `documents`
        3) 依据是否有文档选择创建或复用索引
        异常流：连接串非法或数据库异常将直接抛出。
        """
        # 实现：统一输入类型，保证后续处理一致。
        self.ingest_data = self._prepare_ingest_data()

        documents = []
        for _input in self.ingest_data or []:
            if isinstance(_input, Data):
                documents.append(_input.to_lc_document())
            else:
                documents.append(_input)

        # 注意：连接串可能包含协议/用户信息，统一转换为 `PGVector` 可识别格式。
        connection_string_parsed = transform_connection_string(self.pg_server_url)

        if documents:
            # 决策：有文档时直接写入并创建集合。
            pgvector = PGVector.from_documents(
                embedding=self.embedding,
                documents=documents,
                collection_name=self.collection_name,
                connection_string=connection_string_parsed,
            )
        else:
            # 注意：无文档时复用已有索引，若不存在将抛错。
            pgvector = PGVector.from_existing_index(
                embedding=self.embedding,
                collection_name=self.collection_name,
                connection_string=connection_string_parsed,
            )

        return pgvector

    def search_documents(self) -> list[Data]:
        """执行相似度检索并返回 `Data` 列表。

        契约：当 `search_query` 为空或仅空白时返回空列表。
        排障入口：`status` 会写入检索结果，便于前端显示。
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
