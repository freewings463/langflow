"""
模块名称：Supabase 向量存储组件

本模块封装 Supabase 向量存储的构建与检索逻辑，用于在 Langflow 中完成向量化检索。
主要功能：
- 连接 Supabase 并构建向量存储；
- 按需写入文档并执行相似度检索；
- 输出检索结果为 Data 列表。

关键组件：
- SupabaseVectorStoreComponent：向量存储组件入口。

设计背景：统一 Supabase 的向量检索接入，便于与其他向量库组件对齐。
注意事项：需提供 `supabase_url` 与 `supabase_service_key`，且表结构需与 SupabaseVectorStore 约定一致。
"""

from langchain_community.vectorstores import SupabaseVectorStore
from supabase.client import Client, create_client

from lfx.base.vectorstores.model import LCVectorStoreComponent, check_cached_vector_store
from lfx.helpers.data import docs_to_data
from lfx.io import HandleInput, IntInput, SecretStrInput, StrInput
from lfx.schema.data import Data


class SupabaseVectorStoreComponent(LCVectorStoreComponent):
    """Supabase 向量存储组件

    契约：依赖 Supabase 连接参数与 Embedding；输出向量检索结果 `list[Data]`。
    关键路径：1) 创建 Supabase Client 2) 构建/更新向量存储 3) 执行检索。
    决策：使用 LangChain SupabaseVectorStore 统一接口
    问题：向量存储实现差异导致调用不一致
    方案：通过统一 VectorStore 适配层
    代价：对 LangChain 实现有依赖
    重评：当需要自定义存储实现时
    """
    display_name = "Supabase"
    description = "Supabase Vector Store with search capabilities"
    name = "SupabaseVectorStore"
    icon = "Supabase"

    inputs = [
        StrInput(name="supabase_url", display_name="Supabase URL", required=True),
        SecretStrInput(name="supabase_service_key", display_name="Supabase Service Key", required=True),
        StrInput(name="table_name", display_name="Table Name", advanced=True),
        StrInput(name="query_name", display_name="Query Name"),
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
    def build_vector_store(self) -> SupabaseVectorStore:
        """构建或复用 SupabaseVectorStore

        契约：返回 `SupabaseVectorStore`；可能写入新文档。
        关键路径（三步）：
        1) 创建 Supabase Client
        2) 预处理待写入数据
        3) 有文档则写入，否则直接连接
        异常流：连接或写入失败将由底层抛异常。
        决策：有文档时使用 `from_documents` 写入
        问题：组件需支持首次写入与仅检索两种模式
        方案：根据 documents 是否为空选择构建路径
        代价：首次写入耗时更长
        重评：当引入独立的写入组件时
        """
        supabase: Client = create_client(self.supabase_url, supabase_key=self.supabase_service_key)

        # 注意：预处理输入数据，兼容 DataFrame/Document 等格式。
        self.ingest_data = self._prepare_ingest_data()

        documents = []
        for _input in self.ingest_data or []:
            if isinstance(_input, Data):
                documents.append(_input.to_lc_document())
            else:
                documents.append(_input)

        if documents:
            supabase_vs = SupabaseVectorStore.from_documents(
                documents=documents,
                embedding=self.embedding,
                query_name=self.query_name,
                client=supabase,
                table_name=self.table_name,
            )
        else:
            supabase_vs = SupabaseVectorStore(
                client=supabase,
                embedding=self.embedding,
                table_name=self.table_name,
                query_name=self.query_name,
            )

        return supabase_vs

    def search_documents(self) -> list[Data]:
        """执行相似度检索并返回 Data 列表

        契约：当 `search_query` 有效时返回结果列表，否则返回空列表。
        关键路径：1) 构建向量存储 2) 执行 similarity_search 3) 转换为 Data。
        异常流：查询失败将由底层抛异常。
        决策：空查询直接返回空结果
        问题：空查询会导致无意义的全表扫描
        方案：仅在查询有效时执行检索
        代价：调用方需确保传入查询
        重评：当需要支持空查询的默认行为时
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
