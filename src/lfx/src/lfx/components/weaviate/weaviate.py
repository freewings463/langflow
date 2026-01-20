"""模块名称：Weaviate 向量库组件适配

本模块提供 Weaviate Vector Store 的 Langflow 组件封装，支持向量写入与相似度检索。
使用场景：在检索增强、语义搜索或知识库构建中接入 Weaviate 向量服务。
主要功能包括：
- 构建 Weaviate 客户端并初始化向量库
- 写入文档并基于查询检索
- 支持 API Key 鉴权与按文本检索开关

关键组件：
- WeaviateVectorStoreComponent：Weaviate 向量库组件入口

设计背景：统一 Langflow 向量库接口并兼容 Weaviate 自建/托管集群
注意事项：`index_name` 必须首字母大写，否则会抛 `ValueError`
"""

import weaviate
from langchain_community.vectorstores import Weaviate

from lfx.base.vectorstores.model import LCVectorStoreComponent, check_cached_vector_store
from lfx.helpers.data import docs_to_data
from lfx.io import BoolInput, HandleInput, IntInput, SecretStrInput, StrInput
from lfx.schema.data import Data


class WeaviateVectorStoreComponent(LCVectorStoreComponent):
    """Weaviate 向量库组件，封装写入与检索流程。

    契约：输入 `url`/`index_name`/`api_key` 等，输出向量库或检索结果
    关键路径：1) 构建客户端 2) 校验索引名 3) 写入或检索
    副作用：可能向 Weaviate 写入文档；触发网络请求
    异常流：索引名不符合规则抛 `ValueError`
    排障入口：Weaviate SDK 抛错消息或网络错误
    决策：强制索引名首字母大写
    问题：Weaviate 对索引名有大小写约束
    方案：在构建阶段校验并提示建议值
    代价：对不规范输入直接失败
    重评：当 Weaviate 放宽命名约束或组件改为自动修正时
    """
    display_name = "Weaviate"
    description = "Weaviate Vector Store with search capabilities"
    name = "Weaviate"
    icon = "Weaviate"

    inputs = [
        StrInput(name="url", display_name="Weaviate URL", value="http://localhost:8080", required=True),
        SecretStrInput(name="api_key", display_name="API Key", required=False),
        StrInput(
            name="index_name",
            display_name="Index Name",
            required=True,
            info="Requires capitalized index name.",
        ),
        StrInput(name="text_key", display_name="Text Key", value="text", advanced=True),
        *LCVectorStoreComponent.inputs,
        HandleInput(name="embedding", display_name="Embedding", input_types=["Embeddings"]),
        IntInput(
            name="number_of_results",
            display_name="Number of Results",
            info="Number of results to return.",
            value=4,
            advanced=True,
        ),
        BoolInput(name="search_by_text", display_name="Search By Text", advanced=True),
    ]

    @check_cached_vector_store
    def build_vector_store(self) -> Weaviate:
        """构建 Weaviate 向量库实例并按需写入文档。

        关键路径（三步）：
        1) 选择是否使用 API Key 鉴权
        2) 校验 `index_name` 规则
        3) 规范化输入数据并构建向量库

        契约：返回 `Weaviate` 向量库实例
        副作用：可能进行网络初始化与写入
        异常流：索引名不合法抛 `ValueError`
        """
        if self.api_key:
            auth_config = weaviate.AuthApiKey(api_key=self.api_key)
            client = weaviate.Client(url=self.url, auth_client_secret=auth_config)
        else:
            client = weaviate.Client(url=self.url)

        if self.index_name != self.index_name.capitalize():
            msg = f"Weaviate requires the index name to be capitalized. Use: {self.index_name.capitalize()}"
            raise ValueError(msg)

        # 注意：父类负责将 DataFrame 等输入归一化为可写入的结构。
        self.ingest_data = self._prepare_ingest_data()

        documents = []
        for _input in self.ingest_data or []:
            if isinstance(_input, Data):
                documents.append(_input.to_lc_document())
            else:
                documents.append(_input)

        if documents and self.embedding:
            return Weaviate.from_documents(
                client=client,
                index_name=self.index_name,
                documents=documents,
                embedding=self.embedding,
                by_text=self.search_by_text,
            )

        return Weaviate(
            client=client,
            index_name=self.index_name,
            text_key=self.text_key,
            embedding=self.embedding,
            by_text=self.search_by_text,
        )

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
            )

            data = docs_to_data(docs)
            self.status = data
            return data
        return []
