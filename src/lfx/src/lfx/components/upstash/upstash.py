"""模块名称：Upstash 向量库组件适配

本模块提供 Upstash Vector Store 的 Langflow 组件封装，支持向量写入与相似度检索。
使用场景：在检索增强、语义搜索或知识库构建中接入 Upstash 向量服务。
主要功能包括：
- 构建 Upstash 向量库实例（本地嵌入或 Upstash 内置嵌入）
- 写入文档并基于查询检索
- 支持元数据过滤与命名空间隔离

关键组件：
- UpstashVectorStoreComponent：Upstash 向量库组件入口

设计背景：统一 Langflow 向量库接口并兼容 Upstash 托管服务
注意事项：若不提供 `embedding`，将使用 Upstash 自带嵌入
"""

from langchain_community.vectorstores import UpstashVectorStore

from lfx.base.vectorstores.model import LCVectorStoreComponent, check_cached_vector_store
from lfx.helpers.data import docs_to_data
from lfx.io import (
    HandleInput,
    IntInput,
    MultilineInput,
    SecretStrInput,
    StrInput,
)
from lfx.schema.data import Data


class UpstashVectorStoreComponent(LCVectorStoreComponent):
    """Upstash 向量库组件，封装写入与检索流程。

    契约：输入 `index_url`/`index_token`/`embedding` 等，输出向量库或检索结果
    关键路径：1) 规范化输入数据 2) 构建向量库 3) 写入文档或执行检索
    副作用：可能向 Upstash 写入文档；触发网络请求
    异常流：底层 SDK 异常直接上抛
    排障入口：Upstash SDK 抛错消息或网络错误
    决策：缺省使用 Upstash 内置嵌入
    问题：用户可能未提供 `embedding`
    方案：`embedding` 为空时使用 Upstash 内置嵌入
    代价：嵌入质量与成本受平台默认策略影响
    重评：当产品需要统一嵌入模型或成本策略变更时
    """
    display_name = "Upstash"
    description = "Upstash Vector Store with search capabilities"
    name = "Upstash"
    icon = "Upstash"

    inputs = [
        StrInput(
            name="index_url",
            display_name="Index URL",
            info="The URL of the Upstash index.",
            required=True,
        ),
        SecretStrInput(
            name="index_token",
            display_name="Upstash Index Token",
            info="The token for the Upstash index.",
            required=True,
        ),
        StrInput(
            name="text_key",
            display_name="Text Key",
            info="The key in the record to use as text.",
            value="text",
            advanced=True,
        ),
        StrInput(
            name="namespace",
            display_name="Namespace",
            info="Leave empty for default namespace.",
        ),
        *LCVectorStoreComponent.inputs,
        MultilineInput(
            name="metadata_filter",
            display_name="Metadata Filter",
            info="Filters documents by metadata. Look at the documentation for more information.",
        ),
        HandleInput(
            name="embedding",
            display_name="Embedding",
            input_types=["Embeddings"],
            info="To use Upstash's embeddings, don't provide an embedding.",
        ),
        IntInput(
            name="number_of_results",
            display_name="Number of Results",
            info="Number of results to return.",
            value=4,
            advanced=True,
        ),
    ]

    @check_cached_vector_store
    def build_vector_store(self) -> UpstashVectorStore:
        """构建 Upstash 向量库实例并按需写入文档。

        关键路径（三步）：
        1) 判断嵌入策略并准备 `ingest_data`
        2) 转换为 LangChain 文档列表
        3) 按是否有文档选择创建或批量写入

        契约：返回 `UpstashVectorStore`
        副作用：可能进行网络初始化与写入
        异常流：底层 SDK 异常直接上抛
        """
        use_upstash_embedding = self.embedding is None

        # 注意：父类负责将 DataFrame 等输入归一化为可写入的结构。
        self.ingest_data = self._prepare_ingest_data()

        documents = []
        for _input in self.ingest_data or []:
            if isinstance(_input, Data):
                documents.append(_input.to_lc_document())
            else:
                documents.append(_input)

        if documents:
            if use_upstash_embedding:
                upstash_vs = UpstashVectorStore(
                    embedding=use_upstash_embedding,
                    text_key=self.text_key,
                    index_url=self.index_url,
                    index_token=self.index_token,
                    namespace=self.namespace,
                )
                upstash_vs.add_documents(documents)
            else:
                upstash_vs = UpstashVectorStore.from_documents(
                    documents=documents,
                    embedding=self.embedding,
                    text_key=self.text_key,
                    index_url=self.index_url,
                    index_token=self.index_token,
                    namespace=self.namespace,
                )
        else:
            upstash_vs = UpstashVectorStore(
                embedding=self.embedding or use_upstash_embedding,
                text_key=self.text_key,
                index_url=self.index_url,
                index_token=self.index_token,
                namespace=self.namespace,
            )

        return upstash_vs

    def search_documents(self) -> list[Data]:
        """基于查询执行相似度检索并返回结果。

        契约：仅在 `search_query` 非空时执行检索；返回 `Data` 列表
        副作用：会构建向量库并触发网络查询
        失败语义：空查询直接返回空列表
        排障入口：向量库异常或网络错误消息
        """
        vector_store = self.build_vector_store()

        if self.search_query and isinstance(self.search_query, str) and self.search_query.strip():
            docs = vector_store.similarity_search(
                query=self.search_query,
                k=self.number_of_results,
                filter=self.metadata_filter,
            )

            data = docs_to_data(docs)
            self.status = data
            return data
        return []
