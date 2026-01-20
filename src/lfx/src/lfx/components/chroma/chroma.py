"""
模块名称：Chroma 向量库组件

本模块提供 Chroma 向量库的组件封装，支持索引构建、去重写入与检索。主要功能包括：
- 构建本地或远程 Chroma 向量库连接
- 写入 `Data` 文档并可选去重
- 结合基类实现统一检索输出

关键组件：
- `ChromaVectorStoreComponent`

设计背景：需要在 LFX 组件体系中统一使用 Chroma 向量库。
使用场景：构建向量索引并执行相似度检索。
注意事项：依赖 `langchain-chroma` 与 `chromadb`，缺失将抛 `ImportError`。
"""

from copy import deepcopy
from typing import TYPE_CHECKING

from chromadb.config import Settings
from langchain_chroma import Chroma
from typing_extensions import override

from lfx.base.vectorstores.model import LCVectorStoreComponent, check_cached_vector_store
from lfx.base.vectorstores.utils import chroma_collection_to_data
from lfx.inputs.inputs import BoolInput, DropdownInput, HandleInput, IntInput, StrInput
from lfx.schema.data import Data

if TYPE_CHECKING:
    from lfx.schema.dataframe import DataFrame


class ChromaVectorStoreComponent(LCVectorStoreComponent):
    """Chroma 向量库组件

    契约：输入集合名、持久化目录、embedding、检索参数与 `ingest_data`；输出 `list[Data]`/`DataFrame`；
    副作用：写入向量库、更新 `self.status`、记录日志；
    失败语义：依赖缺失抛 `ImportError`，非 `Data` 输入抛 `TypeError`，Chroma 异常透传。
    关键路径：1) 构建/复用向量库 2) 写入文档并去重 3) 基类检索输出。
    决策：使用 `allow_duplicates` 控制去重写入。
    问题：需要在避免重复写入与性能开销之间平衡。
    方案：可选读取现有文档并过滤重复。
    代价：去重需要额外读取与比对，增加延迟。
    重评：当向量库原生支持去重或 upsert 时。
    """

    display_name: str = "Chroma DB"
    description: str = "Chroma Vector Store with search capabilities"
    name = "Chroma"
    icon = "Chroma"

    inputs = [
        StrInput(
            name="collection_name",
            display_name="Collection Name",
            value="langflow",
        ),
        StrInput(
            name="persist_directory",
            display_name="Persist Directory",
        ),
        *LCVectorStoreComponent.inputs,
        HandleInput(name="embedding", display_name="Embedding", input_types=["Embeddings"]),
        StrInput(
            name="chroma_server_cors_allow_origins",
            display_name="Server CORS Allow Origins",
            advanced=True,
        ),
        StrInput(
            name="chroma_server_host",
            display_name="Server Host",
            advanced=True,
        ),
        IntInput(
            name="chroma_server_http_port",
            display_name="Server HTTP Port",
            advanced=True,
        ),
        IntInput(
            name="chroma_server_grpc_port",
            display_name="Server gRPC Port",
            advanced=True,
        ),
        BoolInput(
            name="chroma_server_ssl_enabled",
            display_name="Server SSL Enabled",
            advanced=True,
        ),
        BoolInput(
            name="allow_duplicates",
            display_name="Allow Duplicates",
            advanced=True,
            info="If false, will not add documents that are already in the Vector Store.",
        ),
        DropdownInput(
            name="search_type",
            display_name="Search Type",
            options=["Similarity", "MMR"],
            value="Similarity",
            advanced=True,
        ),
        IntInput(
            name="number_of_results",
            display_name="Number of Results",
            info="Number of results to return.",
            advanced=True,
            value=10,
        ),
        IntInput(
            name="limit",
            display_name="Limit",
            advanced=True,
            info="Limit the number of records to compare when Allow Duplicates is False.",
        ),
    ]

    @override
    @check_cached_vector_store
    def build_vector_store(self) -> Chroma:
        """构建 Chroma 向量库实例

        契约：读取组件配置并返回 `Chroma`；副作用：可能创建远程客户端、写入文档、更新状态；
        失败语义：依赖缺失抛 `ImportError`，客户端/索引异常透传。
        关键路径（三步）：
        1) 解析服务器配置并创建 Client（可选）
        2) 解析持久化目录并初始化 `Chroma`
        3) 写入文档并刷新 `self.status`
        异常流：依赖缺失或连接失败时抛异常。
        性能瓶颈：写入文档与去重读取可能成为主要耗时点。
        排障入口：日志 `Adding ... documents` 与 `No documents to add`。
        决策：允许同时支持本地持久化与远程服务器模式。
        问题：部署环境可能仅支持本地或远程。
        方案：根据 `chroma_server_host` 选择 Client 模式。
        代价：配置复杂度上升。
        重评：当统一部署模型或引入配置中心时。
        """
        try:
            from chromadb import Client
            from langchain_chroma import Chroma
        except ImportError as e:
            msg = "Could not import Chroma integration package. Please install it with `pip install langchain-chroma`."
            raise ImportError(msg) from e
        chroma_settings = None
        client = None
        if self.chroma_server_host:
            chroma_settings = Settings(
                chroma_server_cors_allow_origins=self.chroma_server_cors_allow_origins or [],
                chroma_server_host=self.chroma_server_host,
                chroma_server_http_port=self.chroma_server_http_port or None,
                chroma_server_grpc_port=self.chroma_server_grpc_port or None,
                chroma_server_ssl_enabled=self.chroma_server_ssl_enabled,
            )
            client = Client(settings=chroma_settings)

        persist_directory = self.resolve_path(self.persist_directory) if self.persist_directory is not None else None

        chroma = Chroma(
            persist_directory=persist_directory,
            client=client,
            embedding_function=self.embedding,
            collection_name=self.collection_name,
        )

        self._add_documents_to_vector_store(chroma)
        limit = int(self.limit) if self.limit is not None and str(self.limit).strip() else None
        self.status = chroma_collection_to_data(chroma.get(limit=limit))
        return chroma

    def _add_documents_to_vector_store(self, vector_store: "Chroma") -> None:
        """将文档写入 Chroma 向量库

        契约：读取 `ingest_data`，写入 `vector_store`；副作用：写入向量库、记录日志、更新状态；
        失败语义：非 `Data` 输入抛 `TypeError`，写入失败异常透传。
        关键路径（三步）：
        1) 规范化输入并准备去重集合
        2) 构建待写入 `Document` 列表
        3) 过滤元数据并写入向量库
        异常流：输入类型不合法或写入失败时抛异常。
        性能瓶颈：去重比对与批量写入。
        排障入口：日志 `Adding ... documents` 与 `No documents to add`。
        决策：去重时移除 `id` 字段以进行内容级比较。
        问题：`id` 不同但内容相同会导致重复写入。
        方案：比较去除 `id` 后的 `Data`。
        代价：需要额外复制与遍历已有数据。
        重评：当向量库支持基于内容的去重或哈希索引时。
        """
        ingest_data: list | Data | DataFrame = self.ingest_data
        if not ingest_data:
            self.status = ""
            return

        ingest_data = self._prepare_ingest_data()

        stored_documents_without_id = []
        if self.allow_duplicates:
            stored_data = []
        else:
            limit = int(self.limit) if self.limit is not None and str(self.limit).strip() else None
            stored_data = chroma_collection_to_data(vector_store.get(limit=limit))
            for value in deepcopy(stored_data):
                del value.id
                stored_documents_without_id.append(value)

        documents = []
        for _input in ingest_data or []:
            if isinstance(_input, Data):
                if _input not in stored_documents_without_id:
                    documents.append(_input.to_lc_document())
            else:
                msg = "Vector Store Inputs must be Data objects."
                raise TypeError(msg)

        if documents and self.embedding is not None:
            self.log(f"Adding {len(documents)} documents to the Vector Store.")
            # 注意：复杂元数据可能触发 ChromaDB 校验失败，先尝试过滤
            try:
                from langchain_community.vectorstores.utils import filter_complex_metadata

                filtered_documents = filter_complex_metadata(documents)
                vector_store.add_documents(filtered_documents)
            except ImportError:
                self.log("Warning: Could not import filter_complex_metadata. Adding documents without filtering.")
                vector_store.add_documents(documents)
        else:
            self.log("No documents to add to the Vector Store.")
