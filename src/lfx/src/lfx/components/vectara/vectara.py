"""
模块名称：Vectara 向量存储组件

模块目的：提供 Vectara 向量存储与相似度检索能力，并输出结构化 `Data`。
使用场景：将文档写入 Vectara，并基于查询进行向量检索。
主要功能包括：
- 构建 Vectara VectorStore 实例并按需写入文档
- 执行相似度检索并转换为 `Data` 列表

关键组件：
- `VectaraVectorStoreComponent`：向量存储组件入口

设计背景：复用 LangChain VectorStore 接口以保持组件一致性。
注意：缺失依赖 `langchain-community` 或鉴权失败会导致构建/调用异常。
"""

from typing import TYPE_CHECKING

from langchain_community.vectorstores import Vectara

from lfx.base.vectorstores.model import LCVectorStoreComponent, check_cached_vector_store
from lfx.helpers.data import docs_to_data
from lfx.io import HandleInput, IntInput, SecretStrInput, StrInput
from lfx.schema.data import Data

if TYPE_CHECKING:
    from lfx.schema.dataframe import DataFrame


class VectaraVectorStoreComponent(LCVectorStoreComponent):
    """Vectara 向量存储组件。

    契约：输入 Vectara 账号信息与可选文档，输出可检索的 VectorStore。
    关键路径：`build_vector_store` 构建实例并写入文档，`search_documents` 执行检索。

    决策：通过 `langchain_community.vectorstores.Vectara` 适配 Vectara
    问题：需要统一向量存储接口并复用现有工具链
    方案：使用 LangChain 社区封装作为适配层
    代价：受上游接口与返回结构稳定性影响
    重评：当上游 API 变更或需原生 SDK 特性时
    """

    display_name: str = "Vectara"
    description: str = "Vectara Vector Store with search capabilities"
    name = "Vectara"
    icon = "Vectara"

    inputs = [
        StrInput(name="vectara_customer_id", display_name="Vectara Customer ID", required=True),
        StrInput(name="vectara_corpus_id", display_name="Vectara Corpus ID", required=True),
        SecretStrInput(name="vectara_api_key", display_name="Vectara API Key", required=True),
        HandleInput(
            name="embedding",
            display_name="Embedding",
            input_types=["Embeddings"],
        ),
        *LCVectorStoreComponent.inputs,
        IntInput(
            name="number_of_results",
            display_name="Number of Results",
            info="Number of results to return.",
            value=4,
            advanced=True,
        ),
    ]

    @check_cached_vector_store
    def build_vector_store(self) -> Vectara:
        """构建并返回 Vectara VectorStore 实例。

        契约：依赖 `vectara_customer_id`/`vectara_corpus_id`/`vectara_api_key`。
        副作用：可能写入向量存储（当 `ingest_data` 非空）。

        关键路径（三步）：
        1) 校验依赖并创建 Vectara 客户端
        2) 处理并写入待入库文档
        3) 返回 VectorStore 供后续检索

        注意：缺少依赖会抛 `ImportError`；鉴权失败在调用阶段抛异常。
        性能：写入耗时与 `ingest_data` 规模线性相关。
        排障：关注导入错误与 Vectara 返回的鉴权/配额错误。
        """
        try:
            from langchain_community.vectorstores import Vectara
        except ImportError as e:
            msg = "Could not import Vectara. Please install it with `pip install langchain-community`."
            raise ImportError(msg) from e

        vectara = Vectara(
            vectara_customer_id=self.vectara_customer_id,
            vectara_corpus_id=self.vectara_corpus_id,
            vectara_api_key=self.vectara_api_key,
        )

        self._add_documents_to_vector_store(vectara)
        return vectara

    def _add_documents_to_vector_store(self, vector_store: Vectara) -> None:
        """将待入库数据转换并写入 VectorStore。

        契约：从 `ingest_data` 读取数据；为空时仅更新 `status`。
        失败语义：数据格式不兼容会导致写入异常向外传播。
        """
        ingest_data: list | Data | DataFrame = self.ingest_data
        if not ingest_data:
            self.status = "No documents to add to Vectara"
            return

        # Convert DataFrame to Data if needed using parent's method
        ingest_data = self._prepare_ingest_data()

        documents = []
        for _input in ingest_data or []:
            if isinstance(_input, Data):
                documents.append(_input.to_lc_document())
            else:
                documents.append(_input)

        if documents:
            self.log(f"Adding {len(documents)} documents to Vectara.")
            vector_store.add_documents(documents)
            self.status = f"Added {len(documents)} documents to Vectara"
        else:
            self.log("No documents to add to Vectara.")
            self.status = "No valid documents to add to Vectara"

    def search_documents(self) -> list[Data]:
        """执行相似度检索并返回结构化结果。

        契约：当 `search_query` 非空时返回 `Data` 列表，否则返回空列表。
        副作用：调用外部 Vectara 服务进行检索（网络 I/O）。

        关键路径（三步）：
        1) 构建/获取 VectorStore
        2) 执行相似度检索
        3) 转换为 `Data` 并写入 `status`

        注意：空查询会直接返回空列表并写入状态提示。
        性能：检索耗时受 `number_of_results` 与远端服务影响。
        排障：关注 Vectara 返回的错误信息与组件 `status`。
        """
        vector_store = self.build_vector_store()

        if self.search_query and isinstance(self.search_query, str) and self.search_query.strip():
            docs = vector_store.similarity_search(
                query=self.search_query,
                k=self.number_of_results,
            )

            data = docs_to_data(docs)
            self.status = f"Found {len(data)} results for the query: {self.search_query}"
            return data
        self.status = "No search query provided"
        return []
