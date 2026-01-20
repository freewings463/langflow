"""
模块名称：AstraDB Graph 向量库组件

本模块提供基于 Astra DB Graph Vector Store 的检索组件，实现图遍历与向量检索结合。主要功能包括：
- 构建 Graph Vector Store 连接与写入文档
- 支持多种搜索类型与过滤参数

关键组件：
- `AstraDBGraphVectorStoreComponent`

设计背景：图结构检索需要专用的向量库实现与搜索策略。
使用场景：基于图遍历的检索增强生成（RAG）。
注意事项：依赖 `langchain-astradb`，缺失将抛 `ImportError`。
"""

import orjson

from lfx.base.datastax.astradb_base import AstraDBBaseComponent
from lfx.base.vectorstores.model import LCVectorStoreComponent, check_cached_vector_store
from lfx.helpers.data import docs_to_data
from lfx.inputs.inputs import (
    DictInput,
    DropdownInput,
    FloatInput,
    IntInput,
    StrInput,
)
from lfx.schema.data import Data


class AstraDBGraphVectorStoreComponent(AstraDBBaseComponent, LCVectorStoreComponent):
    """AstraDB Graph 向量库组件

    契约：输入连接参数、检索配置与 `ingest_data`；输出 `list[Data]`；
    副作用：写入向量库、记录日志、更新 `self.status`；
    失败语义：依赖缺失抛 `ImportError`，搜索/写入异常抛 `ValueError`。
    关键路径：1) 构建向量库 2) 写入文档 3) 执行检索并转换为 `Data`。
    决策：使用 `setup_mode` 控制集合初始化策略。
    问题：不同环境需要不同的初始化/迁移方式。
    方案：将 `setup_mode` 暴露为输入参数。
    代价：错误配置会导致初始化失败或误删。
    重评：当引入统一的环境配置模板时。
    """
    display_name: str = "Astra DB Graph"
    description: str = "Implementation of Graph Vector Store using Astra DB"
    name = "AstraDBGraph"
    documentation: str = "https://docs.langflow.org/bundles-datastax"
    icon: str = "AstraDB"
    legacy: bool = True
    replacement = ["datastax.GraphRAG"]

    inputs = [
        *AstraDBBaseComponent.inputs,
        *LCVectorStoreComponent.inputs,
        StrInput(
            name="metadata_incoming_links_key",
            display_name="Metadata incoming links key",
            info="Metadata key used for incoming links.",
            advanced=True,
        ),
        IntInput(
            name="number_of_results",
            display_name="Number of Results",
            info="Number of results to return.",
            advanced=True,
            value=4,
        ),
        DropdownInput(
            name="search_type",
            display_name="Search Type",
            info="Search type to use",
            options=[
                "Similarity",
                "Similarity with score threshold",
                "MMR (Max Marginal Relevance)",
                "Graph Traversal",
                "MMR (Max Marginal Relevance) Graph Traversal",
            ],
            value="MMR (Max Marginal Relevance) Graph Traversal",
            advanced=True,
        ),
        FloatInput(
            name="search_score_threshold",
            display_name="Search Score Threshold",
            info="Minimum similarity score threshold for search results. "
            "(when using 'Similarity with score threshold')",
            value=0,
            advanced=True,
        ),
        DictInput(
            name="search_filter",
            display_name="Search Metadata Filter",
            info="Optional dictionary of filters to apply to the search query.",
            advanced=True,
            is_list=True,
        ),
    ]

    @check_cached_vector_store
    def build_vector_store(self):
        """构建 AstraDB Graph Vector Store 实例

        契约：返回 `AstraDBGraphVectorStore`；副作用：可能创建集合并写入文档；
        失败语义：依赖缺失抛 `ImportError`，初始化失败抛 `ValueError`。
        关键路径（三步）：1) 解析 `setup_mode` 2) 初始化向量库 3) 写入文档。
        异常流：`setup_mode` 非法或初始化异常。
        性能瓶颈：批量写入与索引构建。
        排障入口：日志 `Initializing Graph Vector Store` 与错误信息。
        决策：使用 `orjson` 解析索引策略字符串。
        问题：索引策略需要以 JSON 字符串传入。
        方案：在构建阶段解析为对象。
        代价：JSON 无效会导致初始化失败。
        重评：当索引策略改为结构化输入时。
        """
        try:
            from langchain_astradb import AstraDBGraphVectorStore
            from langchain_astradb.utils.astradb import SetupMode
        except ImportError as e:
            msg = (
                "Could not import langchain Astra DB integration package. "
                "Please install it with `pip install langchain-astradb`."
            )
            raise ImportError(msg) from e

        try:
            if not self.setup_mode:
                self.setup_mode = self._inputs["setup_mode"].options[0]

            setup_mode_value = SetupMode[self.setup_mode.upper()]
        except KeyError as e:
            msg = f"Invalid setup mode: {self.setup_mode}"
            raise ValueError(msg) from e

        try:
            self.log(f"Initializing Graph Vector Store {self.collection_name}")

            vector_store = AstraDBGraphVectorStore(
                embedding=self.embedding_model,
                collection_name=self.collection_name,
                metadata_incoming_links_key=self.metadata_incoming_links_key or "incoming_links",
                token=self.token,
                api_endpoint=self.get_api_endpoint(),
                namespace=self.get_keyspace(),
                environment=self.environment,
                metric=self.metric or None,
                batch_size=self.batch_size or None,
                bulk_insert_batch_concurrency=self.bulk_insert_batch_concurrency or None,
                bulk_insert_overwrite_concurrency=self.bulk_insert_overwrite_concurrency or None,
                bulk_delete_concurrency=self.bulk_delete_concurrency or None,
                setup_mode=setup_mode_value,
                pre_delete_collection=self.pre_delete_collection,
                metadata_indexing_include=[s for s in self.metadata_indexing_include if s] or None,
                metadata_indexing_exclude=[s for s in self.metadata_indexing_exclude if s] or None,
                collection_indexing_policy=orjson.loads(self.collection_indexing_policy.encode("utf-8"))
                if self.collection_indexing_policy
                else None,
            )
        except Exception as e:
            msg = f"Error initializing AstraDBGraphVectorStore: {e}"
            raise ValueError(msg) from e

        self.log(f"Vector Store initialized: {vector_store.astra_env.collection_name}")
        self._add_documents_to_vector_store(vector_store)

        return vector_store

    def _add_documents_to_vector_store(self, vector_store) -> None:
        """写入文档到 Graph Vector Store

        契约：读取 `ingest_data` 并写入 `vector_store`；副作用：写入库与日志；
        失败语义：非 `Data` 输入抛 `TypeError`，写入失败抛 `ValueError`。
        关键路径：1) 规范化输入 2) 转换为 `Document` 3) 批量写入。
        决策：不在此处做去重，由底层集合处理。
        问题：去重策略可能依赖集合配置。
        方案：直接调用 `add_documents`。
        代价：重复写入可能导致冗余数据。
        重评：当上层需要显式去重时。
        """
        self.ingest_data = self._prepare_ingest_data()

        documents = []
        for _input in self.ingest_data or []:
            if isinstance(_input, Data):
                documents.append(_input.to_lc_document())
            else:
                msg = "Vector Store Inputs must be Data objects."
                raise TypeError(msg)

        if documents:
            self.log(f"Adding {len(documents)} documents to the Vector Store.")
            try:
                vector_store.add_documents(documents)
            except Exception as e:
                msg = f"Error adding documents to AstraDBGraphVectorStore: {e}"
                raise ValueError(msg) from e
        else:
            self.log("No documents to add to the Vector Store.")

    def _map_search_type(self) -> str:
        """映射 UI 搜索类型到后端枚举

        契约：返回搜索类型字符串；副作用：无；失败语义：未知类型回退 `similarity`。
        """
        match self.search_type:
            case "Similarity":
                return "similarity"
            case "Similarity with score threshold":
                return "similarity_score_threshold"
            case "MMR (Max Marginal Relevance)":
                return "mmr"
            case "Graph Traversal":
                return "traversal"
            case "MMR (Max Marginal Relevance) Graph Traversal":
                return "mmr_traversal"
            case _:
                return "similarity"

    def _build_search_args(self):
        """构建搜索参数字典

        契约：返回包含 `k`/`score_threshold`/`filter` 的参数字典；
        副作用：无；失败语义：无。
        """
        args = {
            "k": self.number_of_results,
            "score_threshold": self.search_score_threshold,
        }

        if self.search_filter:
            clean_filter = {k: v for k, v in self.search_filter.items() if k and v}
            if len(clean_filter) > 0:
                args["filter"] = clean_filter
        return args

    def search_documents(self, vector_store=None) -> list[Data]:
        """执行检索并返回 `Data` 列表

        契约：使用 `vector_store` 或构建新实例；输出 `list[Data]`；
        副作用：记录日志并更新 `self.status`；
        失败语义：搜索异常抛 `ValueError`。
        关键路径：1) 生成搜索参数 2) 调用 `search` 3) 转换为 `Data`。
        排障入口：日志 `Searching for documents` 与 `Retrieved documents`。
        """
        if not vector_store:
            vector_store = self.build_vector_store()

        self.log("Searching for documents in AstraDBGraphVectorStore.")
        self.log(f"Search query: {self.search_query}")
        self.log(f"Search type: {self.search_type}")
        self.log(f"Number of results: {self.number_of_results}")

        if self.search_query and isinstance(self.search_query, str) and self.search_query.strip():
            try:
                search_type = self._map_search_type()
                search_args = self._build_search_args()

                docs = vector_store.search(query=self.search_query, search_type=search_type, **search_args)

                # 注意：移除 `links` 以避免非 JSON 元数据导致转换失败
                self.log("Removing links from metadata.")
                for doc in docs:
                    if "links" in doc.metadata:
                        doc.metadata.pop("links")

            except Exception as e:
                msg = f"Error performing search in AstraDBGraphVectorStore: {e}"
                raise ValueError(msg) from e

            self.log(f"Retrieved documents: {len(docs)}")

            data = docs_to_data(docs)

            self.log(f"Converted documents to data: {len(data)}")

            self.status = data
            return data
        self.log("No search input provided. Skipping search.")
        return []

    def get_retriever_kwargs(self):
        """返回检索器参数

        契约：返回 `search_type` 与 `search_kwargs`；副作用：无；失败语义：无。
        """
        search_args = self._build_search_args()
        return {
            "search_type": self._map_search_type(),
            "search_kwargs": search_args,
        }
