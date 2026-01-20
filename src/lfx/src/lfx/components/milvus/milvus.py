"""
模块名称：Milvus 向量库组件

本模块提供 LFX 的 Milvus 组件封装，主要用于配置连接、写入向量并执行相似度检索。主要功能包括：
- 定义 Milvus 组件的输入项与默认值
- 构建并缓存 LangChain Milvus 向量库实例
- 将检索结果转换为 `Data` 返回给流程

关键组件：
- `MilvusVectorStoreComponent`：组件主体
- `build_vector_store`：连接 Milvus 并写入文档
- `search_documents`：相似度检索入口

设计背景：统一向量库组件契约并复用 LangChain Milvus 集成。
使用场景：在流程中接入 Milvus 作为向量存储与检索后端。
注意事项：依赖 `langchain-milvus`；`drop_old=True` 会删除同名集合；网络/权限错误由 SDK 抛出。
"""

from lfx.base.vectorstores.model import LCVectorStoreComponent, check_cached_vector_store
from lfx.helpers.data import docs_to_data
from lfx.io import (
    BoolInput,
    DictInput,
    DropdownInput,
    FloatInput,
    HandleInput,
    IntInput,
    SecretStrInput,
    StrInput,
)
from lfx.schema.data import Data


class MilvusVectorStoreComponent(LCVectorStoreComponent):
    """Milvus 向量库组件，基于 LangChain Milvus 适配。

    契约：输入来自组件表单（如 `collection_name`/`uri`/`embedding`），输出 `search_documents` 的 `list[Data]`。
    副作用：构建时可能创建/删除集合并写入向量；更新 `self.status`。
    失败语义：缺少依赖抛 `ImportError`；连接/检索异常由 LangChain/Milvus SDK 抛出。
    关键路径：`build_vector_store` 构建并写入 -> `search_documents` 执行检索。
    """

    display_name: str = "Milvus"
    description: str = "Milvus vector store with search capabilities"
    name = "Milvus"
    icon = "Milvus"

    inputs = [
        StrInput(name="collection_name", display_name="Collection Name", value="langflow"),
        StrInput(name="collection_description", display_name="Collection Description", value=""),
        StrInput(
            name="uri",
            display_name="Connection URI",
            value="http://localhost:19530",
        ),
        SecretStrInput(
            name="password",
            display_name="Milvus Token",
            value="",
            info="Ignore this field if no token is required to make connection.",
        ),
        DictInput(name="connection_args", display_name="Other Connection Arguments", advanced=True),
        StrInput(name="primary_field", display_name="Primary Field Name", value="pk"),
        StrInput(name="text_field", display_name="Text Field Name", value="text"),
        StrInput(name="vector_field", display_name="Vector Field Name", value="vector"),
        DropdownInput(
            name="consistency_level",
            display_name="Consistencey Level",
            options=["Bounded", "Session", "Strong", "Eventual"],
            value="Session",
            advanced=True,
        ),
        DictInput(name="index_params", display_name="Index Parameters", advanced=True),
        DictInput(name="search_params", display_name="Search Parameters", advanced=True),
        BoolInput(name="drop_old", display_name="Drop Old Collection", value=False, advanced=True),
        FloatInput(name="timeout", display_name="Timeout", advanced=True),
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
    def build_vector_store(self):
        """创建并返回 Milvus 向量库实例，必要时写入文档。

        契约：使用组件字段构建连接参数并返回 LangChain Milvus 实例；当 `ingest_data` 非空时写入文档。
        副作用：可能创建/删除集合（`drop_old`），并执行网络 I/O 与写入。
        关键路径（三步）：1) 校验依赖并构建连接参数 2) 初始化 Milvus 实例 3) 预处理并批量写入文档。
        异常流：缺少 `langchain-milvus` 抛 `ImportError`；连接/写入异常由 SDK 上抛。
        性能瓶颈：`add_documents` 受文档量与向量化成本影响。
        排障入口：ImportError 提示 `pip install langchain-milvus`；Milvus 服务端日志与 SDK 报错信息。
        """
        try:
            from langchain_milvus.vectorstores import Milvus as LangchainMilvus
        except ImportError as e:
            msg = "Could not import Milvus integration package. Please install it with `pip install langchain-milvus`."
            raise ImportError(msg) from e
        self.connection_args.update(uri=self.uri, token=self.password)
        milvus_store = LangchainMilvus(
            embedding_function=self.embedding,
            collection_name=self.collection_name,
            collection_description=self.collection_description,
            connection_args=self.connection_args,
            consistency_level=self.consistency_level,
            index_params=self.index_params,
            search_params=self.search_params,
            drop_old=self.drop_old,
            auto_id=True,
            primary_field=self.primary_field,
            text_field=self.text_field,
            vector_field=self.vector_field,
            timeout=self.timeout,
        )

        # 实现：沿用父类预处理，将 DataFrame 统一为 Data，便于后续转成 LangChain Document。
        self.ingest_data = self._prepare_ingest_data()

        documents = []
        for _input in self.ingest_data or []:
            if isinstance(_input, Data):
                documents.append(_input.to_lc_document())
            else:
                documents.append(_input)

        if documents:
            milvus_store.add_documents(documents)

        return milvus_store

    def search_documents(self) -> list[Data]:
        """执行相似度检索并返回 `Data` 列表。

        契约：结果数量由 `number_of_results` 控制；`search_query` 为空/仅空白时返回空列表。
        副作用：调用 `build_vector_store`（可能触发构建或命中缓存）并更新 `self.status`。
        失败语义：`build_vector_store` 或 `similarity_search` 的异常原样上抛。
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
