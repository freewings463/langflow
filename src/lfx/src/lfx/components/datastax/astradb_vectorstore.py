"""
模块名称：AstraDB 向量库组件

本模块提供 Astra DB Vector Store 的组件封装，支持写入、检索与混合搜索配置。主要功能包括：
- 构建 AstraDBVectorStore 并写入文档
- 支持混合检索/重排与元数据过滤
- 输出标准化 `Data` 结果

关键组件：
- `AstraDBVectorStoreComponent`

设计背景：需要在 LFX 组件体系内统一接入 AstraDB 向量库。
使用场景：文档向量化检索与混合搜索。
注意事项：依赖 `langchain-astradb` 与 Astra API，错误会抛 `ValueError`。
"""

from astrapy import DataAPIClient
from langchain_core.documents import Document

from lfx.base.datastax.astradb_base import AstraDBBaseComponent
from lfx.base.vectorstores.model import LCVectorStoreComponent, check_cached_vector_store
from lfx.base.vectorstores.vector_store_connection_decorator import vector_store_connection
from lfx.helpers.data import docs_to_data
from lfx.io import BoolInput, DropdownInput, FloatInput, HandleInput, IntInput, NestedDictInput, QueryInput, StrInput
from lfx.schema.data import Data
from lfx.serialization import serialize
from lfx.utils.version import get_version_info


@vector_store_connection
class AstraDBVectorStoreComponent(AstraDBBaseComponent, LCVectorStoreComponent):
    """AstraDB 向量库组件

    契约：输入连接参数、检索配置与 `ingest_data`；输出 `list[Data]`/`DataFrame`；
    副作用：写入向量库、记录日志、更新 `self.status`；
    失败语义：依赖缺失抛 `ImportError`，搜索/写入失败抛 `ValueError`。
    关键路径：1) 构建向量库 2) 写入文档 3) 执行检索并转换为 `Data`。
    决策：支持自动检测集合能力并动态调整 UI 配置。
    问题：不同集合支持的混合检索能力不一致。
    方案：在 `update_build_config` 中动态配置显示与默认值。
    代价：构建配置时需要额外请求元数据。
    重评：当集合能力信息可缓存或预先配置时。
    """
    display_name: str = "Astra DB"
    description: str = "Ingest and search documents in Astra DB"
    documentation: str = "https://docs.langflow.org/bundles-datastax"
    name = "AstraDB"
    icon: str = "AstraDB"

    inputs = [
        *AstraDBBaseComponent.inputs,
        *LCVectorStoreComponent.inputs,
        HandleInput(
            name="embedding_model",
            display_name="Embedding Model",
            input_types=["Embeddings"],
            info="Specify the Embedding Model. Not required for Astra Vectorize collections.",
            required=False,
            show=True,
        ),
        StrInput(
            name="content_field",
            display_name="Content Field",
            info="Field to use as the text content field for the vector store.",
            advanced=True,
        ),
        StrInput(
            name="deletion_field",
            display_name="Deletion Based On Field",
            info="When this parameter is provided, documents in the target collection with "
            "metadata field values matching the input metadata field value will be deleted "
            "before new data is loaded.",
            advanced=True,
        ),
        BoolInput(
            name="ignore_invalid_documents",
            display_name="Ignore Invalid Documents",
            info="Boolean flag to determine whether to ignore invalid documents at runtime.",
            advanced=True,
        ),
        NestedDictInput(
            name="astradb_vectorstore_kwargs",
            display_name="AstraDBVectorStore Parameters",
            info="Optional dictionary of additional parameters for the AstraDBVectorStore.",
            advanced=True,
        ),
        DropdownInput(
            name="search_method",
            display_name="Search Method",
            info=(
                "Determine how your content is matched: Vector finds semantic similarity, "
                "and Hybrid Search (suggested) combines both approaches "
                "with a reranker."
            ),
            options=["Hybrid Search", "Vector Search"],  # TODO：恢复 Lexical Search？
            options_metadata=[{"icon": "SearchHybrid"}, {"icon": "SearchVector"}],
            value="Vector Search",
            advanced=True,
            real_time_refresh=True,
        ),
        DropdownInput(
            name="reranker",
            display_name="Reranker",
            info="Post-retrieval model that re-scores results for optimal relevance ranking.",
            show=False,
            toggle=True,
        ),
        QueryInput(
            name="lexical_terms",
            display_name="Lexical Terms",
            info="Add additional terms/keywords to augment search precision.",
            placeholder="Enter terms to search...",
            separator=" ",
            show=False,
            value="",
        ),
        IntInput(
            name="number_of_results",
            display_name="Number of Search Results",
            info="Number of search results to return.",
            advanced=True,
            value=4,
        ),
        DropdownInput(
            name="search_type",
            display_name="Search Type",
            info="Search type to use",
            options=["Similarity", "Similarity with score threshold", "MMR (Max Marginal Relevance)"],
            value="Similarity",
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
        NestedDictInput(
            name="advanced_search_filter",
            display_name="Search Metadata Filter",
            info="Optional dictionary of filters to apply to the search query.",
            advanced=True,
        ),
    ]

    async def update_build_config(
        self,
        build_config: dict,
        field_value: str | dict,
        field_name: str | None = None,
    ) -> dict:
        """更新构建配置并同步检索相关显示逻辑

        契约：输入构建配置并返回更新后的配置；副作用：可能触发集合能力探测；
        失败语义：底层查询异常会中断构建配置。
        关键路径：1) 调用基类更新 2) 设置 embedding 可见性 3) 配置检索选项。
        决策：当集合使用 Vectorize 时隐藏 embedding 输入。
        问题：服务端向量化不需要本地 embedding。
        方案：根据 provider 显示/隐藏 embedding 字段。
        代价：依赖集合元数据准确性。
        重评：当 embedding 选择逻辑改为显式输入时。
        """
        build_config = await super().update_build_config(
            build_config,
            field_value=field_value,
            field_name=field_name,
        )

        if isinstance(field_value, dict) and "02_embedding_generation_provider" in field_value:
            embedding_provider = field_value.get("02_embedding_generation_provider")
            is_custom_provider = embedding_provider and embedding_provider != "Bring your own"
            provider = embedding_provider.lower() if is_custom_provider and embedding_provider is not None else None

            build_config["embedding_model"]["show"] = not bool(provider)
            build_config["embedding_model"]["required"] = not bool(provider)

        if not self.get_api_endpoint():
            return build_config

        return self._configure_search_options(build_config)

    def _configure_search_options(self, build_config: dict) -> dict:
        """配置混合检索与重排相关选项

        契约：输入构建配置并返回更新后的配置；
        副作用：可能调用集合能力探测；
        失败语义：探测异常将回退为禁用混合检索。
        关键路径：1) 探测能力 2) 调整检索方法与重排配置 3) 同步字段显隐。
        决策：当集合不支持混合检索时强制 Vector Search。
        问题：不支持的能力会导致运行时失败。
        方案：在构建阶段隐藏相关选项。
        代价：用户无法选择不支持的模式。
        重评：当服务端能力稳定且可缓存时。
        """
        hybrid_capabilities = self._detect_hybrid_capabilities()

        if not build_config["collection_name"]["options"] or not build_config["collection_name"]["value"]:
            return build_config

        collection_options = self._get_collection_options(build_config)

        index = build_config["collection_name"]["options"].index(build_config["collection_name"]["value"])
        provider = build_config["collection_name"]["options_metadata"][index]["provider"]
        build_config["embedding_model"]["show"] = not bool(provider)
        build_config["embedding_model"]["required"] = not bool(provider)

        is_vector_search = build_config["search_method"]["value"] == "Vector Search"
        is_autodetect = build_config["autodetect_collection"]["value"]

        if hybrid_capabilities["available"]:
            build_config["search_method"]["show"] = True
            build_config["search_method"]["options"] = ["Hybrid Search", "Vector Search"]
            build_config["search_method"]["value"] = build_config["search_method"].get("value", "Hybrid Search")

            build_config["reranker"]["options"] = hybrid_capabilities["reranker_models"]
            build_config["reranker"]["options_metadata"] = hybrid_capabilities["reranker_metadata"]
            if hybrid_capabilities["reranker_models"]:
                build_config["reranker"]["value"] = hybrid_capabilities["reranker_models"][0]
        else:
            build_config["search_method"]["show"] = False
            build_config["search_method"]["options"] = ["Vector Search"]
            build_config["search_method"]["value"] = "Vector Search"
            build_config["reranker"]["options"] = []
            build_config["reranker"]["options_metadata"] = []

        hybrid_enabled = (
            collection_options["rerank_enabled"] and build_config["search_method"]["value"] == "Hybrid Search"
        )

        build_config["reranker"]["show"] = hybrid_enabled
        build_config["reranker"]["toggle_value"] = hybrid_enabled
        build_config["reranker"]["toggle_disable"] = is_vector_search

        lexical_visible = collection_options["lexical_enabled"] and not is_vector_search
        build_config["lexical_terms"]["show"] = lexical_visible
        build_config["lexical_terms"]["value"] = "" if is_vector_search else build_config["lexical_terms"]["value"]

        build_config["search_type"]["show"] = is_vector_search
        build_config["search_score_threshold"]["show"] = is_vector_search

        if hybrid_enabled or is_autodetect:
            build_config["search_type"]["value"] = "Similarity"

        return build_config

    def _detect_hybrid_capabilities(self) -> dict:
        """探测混合检索与重排能力

        契约：返回能力字典，包含可用性与模型列表；
        副作用：访问 Astra Admin API；
        失败语义：异常时返回不可用并记录日志。
        关键路径：1) 获取 admin client 2) 查询 reranker provider 3) 构建模型列表。
        决策：探测失败时降级为不可用。
        问题：能力探测失败不应阻断组件加载。
        方案：捕获异常并返回不可用。
        代价：隐藏可能可用的功能。
        重评：当探测结果可缓存或可配置时。
        """
        environment = self.get_environment(self.environment)
        client = DataAPIClient(environment=environment)
        admin_client = client.get_admin()
        db_admin = admin_client.get_database_admin(self.get_api_endpoint(), token=self.token)

        try:
            providers = db_admin.find_reranking_providers()
            reranker_models = [
                model.name for provider_data in providers.reranking_providers.values() for model in provider_data.models
            ]
            reranker_metadata = [
                {"icon": self.get_provider_icon(provider_name=model.name.split("/")[0])}
                for provider in providers.reranking_providers.values()
                for model in provider.models
            ]
        except Exception as e:  # noqa: BLE001
            self.log(f"Hybrid search not available: {e}")
            return {
                "available": False,
                "reranker_models": [],
                "reranker_metadata": [],
            }
        else:
            return {
                "available": True,
                "reranker_models": reranker_models,
                "reranker_metadata": reranker_metadata,
            }

    def _get_collection_options(self, build_config: dict) -> dict:
        """获取集合级检索配置

        契约：返回 `rerank_enabled` 与 `lexical_enabled`；
        副作用：访问集合元数据；
        失败语义：异常透传。
        """
        database = self.get_database_object(api_endpoint=build_config["api_endpoint"]["value"])
        collection = database.get_collection(
            name=build_config["collection_name"]["value"],
            keyspace=build_config["keyspace"]["value"],
        )

        col_options = collection.options()

        return {
            "rerank_enabled": bool(col_options.rerank and col_options.rerank.enabled),
            "lexical_enabled": bool(col_options.lexical and col_options.lexical.enabled),
        }

    @check_cached_vector_store
    def build_vector_store(self):
        """构建 AstraDBVectorStore 实例

        契约：返回向量库实例；副作用：可能创建集合并写入文档；
        失败语义：依赖缺失抛 `ImportError`，初始化失败抛 `ValueError`。
        关键路径（三步）：1) 组装参数 2) 创建 `AstraDBVectorStore` 3) 写入文档。
        异常流：参数不兼容或 API 初始化失败。
        性能瓶颈：写入文档与集合自动检测。
        排障入口：日志与异常消息。
        决策：在服务端向量化时可不提供本地 embedding。
        问题：Vectorize 集合不需要本地嵌入模型。
        方案：按 provider 判断 embedding 是否必须。
        代价：自动检测逻辑依赖集合元数据。
        重评：当统一采用显式配置时。
        """
        try:
            from langchain_astradb import AstraDBVectorStore
            from langchain_astradb.utils.astradb import HybridSearchMode
        except ImportError as e:
            msg = (
                "Could not import langchain Astra DB integration package. "
                "Please install it with `pip install langchain-astradb`."
            )
            raise ImportError(msg) from e

        embedding_params = {"embedding": self.embedding_model} if self.embedding_model else {}

        additional_params = self.astradb_vectorstore_kwargs or {}

        __version__ = get_version_info()["version"]
        langflow_prefix = ""

        database = self.get_database_object()
        autodetect = self.collection_name in database.list_collection_names() and self.autodetect_collection

        autodetect_params = {
            "autodetect_collection": autodetect,
            "content_field": (
                self.content_field
                if self.content_field and embedding_params
                else (
                    "page_content"
                    if embedding_params
                    and self.collection_data(collection_name=self.collection_name, database=database) == 0
                    else None
                )
            ),
            "ignore_invalid_documents": self.ignore_invalid_documents,
        }

        hybrid_search_mode = HybridSearchMode.DEFAULT if self.search_method == "Hybrid Search" else HybridSearchMode.OFF

        try:
            vector_store = AstraDBVectorStore(
                token=self.token,
                api_endpoint=database.api_endpoint,
                namespace=database.keyspace,
                collection_name=self.collection_name,
                environment=self.environment,
                hybrid_search=hybrid_search_mode,
                ext_callers=[(f"{langflow_prefix}langflow", __version__)],
                **autodetect_params,
                **embedding_params,
                **additional_params,
            )
        except ValueError as e:
            msg = f"Error initializing AstraDBVectorStore: {e}"
            raise ValueError(msg) from e

        self._add_documents_to_vector_store(vector_store)

        return vector_store

    def _add_documents_to_vector_store(self, vector_store) -> None:
        """写入文档到 AstraDB 向量库

        契约：读取 `ingest_data` 并写入 `vector_store`；副作用：可能删除旧文档并写入新文档；
        失败语义：非 `Data` 输入抛 `TypeError`，写入失败抛 `ValueError`。
        关键路径：1) 规范化输入 2) 处理删除策略 3) 批量写入。
        决策：当 `deletion_field` 设置时先删除匹配文档。
        问题：需要保证幂等写入或覆盖旧数据。
        方案：基于元数据字段进行删除。
        代价：删除操作增加额外成本。
        重评：当支持 upsert 或版本化写入时。
        """
        self.ingest_data = self._prepare_ingest_data()

        documents = []
        for _input in self.ingest_data or []:
            if isinstance(_input, Data):
                documents.append(_input.to_lc_document())
            else:
                msg = "Vector Store Inputs must be Data objects."
                raise TypeError(msg)

        documents = [
            Document(page_content=doc.page_content, metadata=serialize(doc.metadata, to_str=True)) for doc in documents
        ]

        if documents and self.deletion_field:
            self.log(f"Deleting documents where {self.deletion_field}")
            try:
                database = self.get_database_object()
                collection = database.get_collection(self.collection_name, keyspace=database.keyspace)
                delete_values = list({doc.metadata[self.deletion_field] for doc in documents})
                self.log(f"Deleting documents where {self.deletion_field} matches {delete_values}.")
                collection.delete_many({f"metadata.{self.deletion_field}": {"$in": delete_values}})
            except ValueError as e:
                msg = f"Error deleting documents from AstraDBVectorStore based on '{self.deletion_field}': {e}"
                raise ValueError(msg) from e

        if documents:
            self.log(f"Adding {len(documents)} documents to the Vector Store.")
            try:
                vector_store.add_documents(documents)
            except ValueError as e:
                msg = f"Error adding documents to AstraDBVectorStore: {e}"
                raise ValueError(msg) from e
        else:
            self.log("No documents to add to the Vector Store.")

    def _map_search_type(self) -> str:
        """映射搜索类型到后端枚举

        契约：返回后端 search_type；副作用：无；失败语义：未知类型回退 `similarity`。
        """
        search_type_mapping = {
            "Similarity with score threshold": "similarity_score_threshold",
            "MMR (Max Marginal Relevance)": "mmr",
        }

        return search_type_mapping.get(self.search_type, "similarity")

    def _build_search_args(self):
        """构建搜索参数

        契约：返回搜索参数字典或空字典；
        副作用：无；失败语义：无。
        决策：无有效查询且无过滤时返回空。
        问题：避免在无查询时发起搜索。
        方案：空参数即跳过搜索。
        代价：需要调用方判断空返回。
        重评：当需要默认检索行为时。
        """
        query = self.search_query if isinstance(self.search_query, str) and self.search_query.strip() else None
        lexical_terms = self.lexical_terms or None

        if query:
            args = {
                "query": query,
                "search_type": self._map_search_type(),
                "k": self.number_of_results,
                "score_threshold": self.search_score_threshold,
                "lexical_query": lexical_terms,
            }
        elif self.advanced_search_filter:
            args = {
                "n": self.number_of_results,
            }
        else:
            return {}

        filter_arg = self.advanced_search_filter or {}
        if filter_arg:
            args["filter"] = filter_arg

        return args

    def search_documents(self, vector_store=None) -> list[Data]:
        """执行检索并返回 `Data` 列表

        契约：使用 `vector_store` 或构建新实例；输出 `list[Data]`；
        副作用：记录日志并更新 `self.status`；
        失败语义：搜索异常抛 `ValueError`。
        关键路径：1) 构建搜索参数 2) 调用 search/metadata_search 3) 转换为 `Data`。
        排障入口：日志 `Calling vector_store...` 与 `Retrieved documents`。
        """
        vector_store = vector_store or self.build_vector_store()

        self.log(f"Search input: {self.search_query}")
        self.log(f"Search type: {self.search_type}")
        self.log(f"Number of results: {self.number_of_results}")
        self.log(f"store.hybrid_search: {vector_store.hybrid_search}")
        self.log(f"Lexical terms: {self.lexical_terms}")
        self.log(f"Reranker: {self.reranker}")

        try:
            search_args = self._build_search_args()
        except ValueError as e:
            msg = f"Error in AstraDBVectorStore._build_search_args: {e}"
            raise ValueError(msg) from e

        if not search_args:
            self.log("No search input or filters provided. Skipping search.")
            return []

        docs = []
        search_method = "search" if "query" in search_args else "metadata_search"

        try:
            self.log(f"Calling vector_store.{search_method} with args: {search_args}")
            docs = getattr(vector_store, search_method)(**search_args)
        except ValueError as e:
            msg = f"Error performing {search_method} in AstraDBVectorStore: {e}"
            raise ValueError(msg) from e

        self.log(f"Retrieved documents: {len(docs)}")

        data = docs_to_data(docs)
        self.log(f"Converted documents to data: {len(data)}")
        self.status = data

        return data

    def get_retriever_kwargs(self):
        """返回检索器参数

        契约：返回 `search_type` 与 `search_kwargs`；副作用：无；失败语义：无。
        """
        search_args = self._build_search_args()

        return {
            "search_type": self._map_search_type(),
            "search_kwargs": search_args,
        }
