"""
模块名称：Cassandra 图向量存储组件

本模块提供 Cassandra 图向量存储的构建与检索能力。
主要功能包括：
- 初始化 Cassandra/Astra 连接并构建图向量存储
- 支持 Traversal/MMR/相似度等检索模式
- 将检索结果统一转换为 Langflow `Data`

关键组件：
- CassandraGraphVectorStoreComponent：图向量检索组件

设计背景：为图结构向量检索提供统一的组件接口。
注意事项：依赖 `cassio` 包；检索参数需与所选搜索类型匹配。
"""

from uuid import UUID

from langchain_community.graph_vectorstores import CassandraGraphVectorStore

from lfx.base.vectorstores.model import LCVectorStoreComponent, check_cached_vector_store
from lfx.helpers.data import docs_to_data
from lfx.inputs.inputs import DictInput, FloatInput
from lfx.io import (
    DropdownInput,
    HandleInput,
    IntInput,
    MessageTextInput,
    SecretStrInput,
)
from lfx.schema.data import Data


class CassandraGraphVectorStoreComponent(LCVectorStoreComponent):
    """Cassandra 图向量存储组件。

    契约：必须提供 `embedding`、`database_ref`、`keyspace` 与 `table_name`。
    副作用：初始化 cassio 连接并可能创建图向量表。
    失败语义：缺少 `cassio` 时抛 `ImportError`；检索阶段可能抛 `ValueError`。
    """

    display_name = "Cassandra Graph"
    description = "Cassandra Graph Vector Store"
    name = "CassandraGraph"
    icon = "Cassandra"

    inputs = [
        MessageTextInput(
            name="database_ref",
            display_name="Contact Points / Astra Database ID",
            info="Contact points for the database (or Astra DB database ID)",
            required=True,
        ),
        MessageTextInput(
            name="username", display_name="Username", info="Username for the database (leave empty for Astra DB)."
        ),
        SecretStrInput(
            name="token",
            display_name="Password / Astra DB Token",
            info="User password for the database (or Astra DB token).",
            required=True,
        ),
        MessageTextInput(
            name="keyspace",
            display_name="Keyspace",
            info="Table Keyspace (or Astra DB namespace).",
            required=True,
        ),
        MessageTextInput(
            name="table_name",
            display_name="Table Name",
            info="The name of the table (or Astra DB collection) where vectors will be stored.",
            required=True,
        ),
        DropdownInput(
            name="setup_mode",
            display_name="Setup Mode",
            info="Configuration mode for setting up the Cassandra table, with options like 'Sync' or 'Off'.",
            options=["Sync", "Off"],
            value="Sync",
            advanced=True,
        ),
        DictInput(
            name="cluster_kwargs",
            display_name="Cluster arguments",
            info="Optional dictionary of additional keyword arguments for the Cassandra cluster.",
            advanced=True,
            list=True,
        ),
        *LCVectorStoreComponent.inputs,
        HandleInput(name="embedding", display_name="Embedding", input_types=["Embeddings"]),
        IntInput(
            name="number_of_results",
            display_name="Number of Results",
            info="Number of results to return.",
            value=4,
            advanced=True,
        ),
        DropdownInput(
            name="search_type",
            display_name="Search Type",
            info="Search type to use",
            options=[
                "Traversal",
                "MMR traversal",
                "Similarity",
                "Similarity with score threshold",
                "MMR (Max Marginal Relevance)",
            ],
            value="Traversal",
            advanced=True,
        ),
        IntInput(
            name="depth",
            display_name="Depth of traversal",
            info="The maximum depth of edges to traverse. (when using 'Traversal' or 'MMR traversal')",
            value=1,
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
            list=True,
        ),
    ]

    @check_cached_vector_store
    def build_vector_store(self) -> CassandraGraphVectorStore:
        """构建图向量存储实例。

        关键路径（三步）：
        1) 校验依赖并初始化 cassio 连接。
        2) 规范化输入数据为文档列表。
        3) 依据是否有文档选择写入或仅构建表。

        异常流：缺少 `cassio` 依赖时抛 `ImportError`；连接初始化失败时异常原样透传。
        性能瓶颈：文档写入与向量化为主要耗时。
        """
        try:
            import cassio
            from langchain_community.utilities.cassandra import SetupMode
        except ImportError as e:
            msg = "Could not import cassio integration package. Please install it with `pip install cassio`."
            raise ImportError(msg) from e

        database_ref = self.database_ref

        try:
            UUID(self.database_ref)
            is_astra = True
        except ValueError:
            is_astra = False
            if "," in self.database_ref:
                # 注意：不改变字段类型，拆分后使用局部变量
                database_ref = self.database_ref.split(",")

        if is_astra:
            cassio.init(
                database_id=database_ref,
                token=self.token,
                cluster_kwargs=self.cluster_kwargs,
            )
        else:
            cassio.init(
                contact_points=database_ref,
                username=self.username,
                password=self.token,
                cluster_kwargs=self.cluster_kwargs,
            )

        self.ingest_data = self._prepare_ingest_data()

        documents = []

        for _input in self.ingest_data or []:
            if isinstance(_input, Data):
                documents.append(_input.to_lc_document())
            else:
                documents.append(_input)

        setup_mode = SetupMode.OFF if self.setup_mode == "Off" else SetupMode.SYNC

        if documents:
            self.log(f"Adding {len(documents)} documents to the Vector Store.")
            store = CassandraGraphVectorStore.from_documents(
                documents=documents,
                embedding=self.embedding,
                node_table=self.table_name,
                keyspace=self.keyspace,
            )
        else:
            self.log("No documents to add to the Vector Store.")
            store = CassandraGraphVectorStore(
                embedding=self.embedding,
                node_table=self.table_name,
                keyspace=self.keyspace,
                setup_mode=setup_mode,
            )
        return store

    def _map_search_type(self) -> str:
        """将界面搜索类型映射为图向量检索枚举值。

        输入：无（读取 `self.search_type`）。
        输出：字符串枚举（`traversal`/`mmr_traversal`/`similarity` 等）。
        """
        if self.search_type == "Similarity":
            return "similarity"
        if self.search_type == "Similarity with score threshold":
            return "similarity_score_threshold"
        if self.search_type == "MMR (Max Marginal Relevance)":
            return "mmr"
        if self.search_type == "MMR Traversal":
            return "mmr_traversal"
        return "traversal"

    def search_documents(self) -> list[Data]:
        """执行图向量检索并返回标准化数据。

        关键路径（三步）：
        1) 构建/获取图向量存储实例。
        2) 组装检索参数并执行搜索。
        3) 将检索结果转为 `Data` 并写入 `status`。

        异常流：集合无 `content` 字段时抛 `ValueError`。
        副作用：写入 `self.status` 并产生日志。
        """
        vector_store = self.build_vector_store()

        self.log(f"Search input: {self.search_query}")
        self.log(f"Search type: {self.search_type}")
        self.log(f"Number of results: {self.number_of_results}")

        if self.search_query and isinstance(self.search_query, str) and self.search_query.strip():
            try:
                search_type = self._map_search_type()
                search_args = self._build_search_args()

                self.log(f"Search args: {search_args}")

                docs = vector_store.search(query=self.search_query, search_type=search_type, **search_args)
            except KeyError as e:
                if "content" in str(e):
                    msg = (
                        "You should ingest data through Langflow (or LangChain) to query it in Langflow. "
                        "Your collection does not contain a field name 'content'."
                    )
                    raise ValueError(msg) from e
                raise

            self.log(f"Retrieved documents: {len(docs)}")

            data = docs_to_data(docs)
            self.status = data
            return data
        return []

    def _build_search_args(self):
        """构建图向量检索参数字典。

        输入：无（读取组件配置）。
        输出：包含 `k/score_threshold/depth` 等参数的字典。
        """
        args = {
            "k": self.number_of_results,
            "score_threshold": self.search_score_threshold,
            "depth": self.depth,
        }

        if self.search_filter:
            clean_filter = {k: v for k, v in self.search_filter.items() if k and v}
            if len(clean_filter) > 0:
                args["filter"] = clean_filter
        return args

    def get_retriever_kwargs(self):
        """提供检索器所需的参数配置。

        输入：无。
        输出：包含 `search_type` 与 `search_kwargs` 的字典。
        """
        search_args = self._build_search_args()
        return {
            "search_type": self._map_search_type(),
            "search_kwargs": search_args,
        }
