"""
模块名称：ClickHouse 向量存储组件

本模块提供 ClickHouse 向量存储的连接、写入与相似度检索能力，主要用于在 Langflow
中构建基于 ClickHouse 的向量检索组件。主要功能包括：
- 校验 ClickHouse 连接并初始化向量存储
- 将输入数据转换为 LangChain 文档并写入
- 执行向量相似度查询并输出为 `Data`

关键组件：
- `ClickhouseVectorStoreComponent`：ClickHouse 向量存储组件入口

设计背景：为使用 ClickHouse 的向量检索场景提供统一组件封装。
注意事项：依赖 `clickhouse-connect`，连接失败将抛 `ValueError`。
"""

from langchain_community.vectorstores import Clickhouse, ClickhouseSettings

from lfx.base.vectorstores.model import LCVectorStoreComponent, check_cached_vector_store
from lfx.helpers.data import docs_to_data
from lfx.inputs.inputs import BoolInput, FloatInput
from lfx.io import (
    DictInput,
    DropdownInput,
    HandleInput,
    IntInput,
    SecretStrInput,
    StrInput,
)
from lfx.schema.data import Data


class ClickhouseVectorStoreComponent(LCVectorStoreComponent):
    display_name = "ClickHouse"
    description = "ClickHouse Vector Store with search capabilities"
    name = "Clickhouse"
    icon = "Clickhouse"

    inputs = [
        StrInput(name="host", display_name="hostname", required=True, value="localhost"),
        IntInput(name="port", display_name="port", required=True, value=8123),
        StrInput(name="database", display_name="database", required=True),
        StrInput(name="table", display_name="Table name", required=True),
        StrInput(name="username", display_name="The ClickHouse user name.", required=True),
        SecretStrInput(name="password", display_name="Clickhouse Password", required=True),
        DropdownInput(
            name="index_type",
            display_name="index_type",
            options=["annoy", "vector_similarity"],
            info="Type of the index.",
            value="annoy",
            advanced=True,
        ),
        DropdownInput(
            name="metric",
            display_name="metric",
            options=["angular", "euclidean", "manhattan", "hamming", "dot"],
            info="Metric to compute distance.",
            value="angular",
            advanced=True,
        ),
        BoolInput(
            name="secure",
            display_name="Use https/TLS. This overrides inferred values from the interface or port arguments.",
            value=False,
            advanced=True,
        ),
        StrInput(name="index_param", display_name="Param of the index", value="100,'L2Distance'", advanced=True),
        DictInput(name="index_query_params", display_name="index query params", advanced=True),
        *LCVectorStoreComponent.inputs,
        HandleInput(name="embedding", display_name="Embedding", input_types=["Embeddings"]),
        IntInput(
            name="number_of_results",
            display_name="Number of Results",
            info="Number of results to return.",
            value=4,
            advanced=True,
        ),
        FloatInput(name="score_threshold", display_name="Score threshold", advanced=True),
    ]

    @check_cached_vector_store
    def build_vector_store(self) -> Clickhouse:
        """构建 ClickHouse 向量存储实例。

        契约：输入为组件字段与 `ingest_data`，输出 `Clickhouse` 实例。
        关键路径（三步）：
        1) 校验依赖并测试 ClickHouse 连接。
        2) 预处理输入数据并构建文档列表。
        3) 生成 `ClickhouseSettings` 并实例化向量存储。

        异常流：依赖缺失抛 `ImportError`；连接失败抛 `ValueError`。
        排障入口：错误消息前缀 `Failed to connect to Clickhouse`。
        """
        try:
            import clickhouse_connect
        except ImportError as e:
            msg = (
                "Failed to import ClickHouse dependencies. "
                "Install it using `uv pip install langflow[clickhouse-connect] --pre`"
            )
            raise ImportError(msg) from e

        try:
            client = clickhouse_connect.get_client(
                host=self.host, port=self.port, username=self.username, password=self.password
            )
            client.command("SELECT 1")
        except Exception as e:
            msg = f"Failed to connect to Clickhouse: {e}"
            raise ValueError(msg) from e

        self.ingest_data = self._prepare_ingest_data()

        documents = []
        for _input in self.ingest_data or []:
            if isinstance(_input, Data):
                documents.append(_input.to_lc_document())
            else:
                documents.append(_input)

        kwargs = {}
        if self.index_param:
            kwargs["index_param"] = self.index_param.split(",")
        if self.index_query_params:
            kwargs["index_query_params"] = self.index_query_params

        settings = ClickhouseSettings(
            table=self.table,
            database=self.database,
            host=self.host,
            index_type=self.index_type,
            metric=self.metric,
            password=self.password,
            port=self.port,
            secure=self.secure,
            username=self.username,
            **kwargs,
        )
        if documents:
            clickhouse_vs = Clickhouse.from_documents(documents=documents, embedding=self.embedding, config=settings)

        else:
            clickhouse_vs = Clickhouse(embedding=self.embedding, config=settings)

        return clickhouse_vs

    def search_documents(self) -> list[Data]:
        """执行向量相似度检索。

        契约：输入为 `search_query/number_of_results/score_threshold`，输出 `Data` 列表。
        副作用：会触发向量存储连接与查询，并更新 `self.status`。
        失败语义：查询条件为空时返回空列表。
        """
        vector_store = self.build_vector_store()

        if self.search_query and isinstance(self.search_query, str) and self.search_query.strip():
            kwargs = {}
            if self.score_threshold:
                kwargs["score_threshold"] = self.score_threshold

            docs = vector_store.similarity_search(query=self.search_query, k=self.number_of_results, **kwargs)

            data = docs_to_data(docs)
            self.status = data
            return data
        return []
