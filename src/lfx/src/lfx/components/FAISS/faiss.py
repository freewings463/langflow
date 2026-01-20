"""
模块名称：faiss

本模块提供 FAISS 向量库组件封装，支持构建索引与相似度检索。
主要功能包括：
- 构建并持久化 FAISS 索引
- 基于查询进行向量相似度搜索

关键组件：
- `FaissVectorStoreComponent`：FAISS 组件

设计背景：需要本地轻量向量检索能力
使用场景：小型/中型数据集的向量检索
注意事项：本地索引依赖磁盘路径与序列化安全配置
"""

from pathlib import Path

from langchain_community.vectorstores import FAISS

from lfx.base.vectorstores.model import LCVectorStoreComponent, check_cached_vector_store
from lfx.helpers.data import docs_to_data
from lfx.io import BoolInput, HandleInput, IntInput, StrInput
from lfx.schema.data import Data


class FaissVectorStoreComponent(LCVectorStoreComponent):
    """FAISS 向量库组件。

    契约：提供 `embedding` 与可索引数据，支持构建与检索。
    副作用：读写本地索引文件并更新 `ingest_data`。
    失败语义：索引构建/加载失败时抛异常。
    决策：使用本地 FAISS 作为默认向量存储。
    问题：需要轻量、易部署的向量检索能力。
    方案：采用 FAISS 并提供本地持久化。
    代价：不适合分布式与超大规模数据。
    重评：当数据规模或并发需求超出单机能力时。
    """

    display_name: str = "FAISS"
    description: str = "FAISS Vector Store with search capabilities"
    name = "FAISS"
    icon = "FAISS"

    inputs = [
        StrInput(
            name="index_name",
            display_name="Index Name",
            value="langflow_index",
        ),
        StrInput(
            name="persist_directory",
            display_name="Persist Directory",
            info="Path to save the FAISS index. It will be relative to where Langflow is running.",
        ),
        *LCVectorStoreComponent.inputs,
        BoolInput(
            name="allow_dangerous_deserialization",
            display_name="Allow Dangerous Deserialization",
            info="Set to True to allow loading pickle files from untrusted sources. "
            "Only enable this if you trust the source of the data.",
            advanced=True,
            value=True,
        ),
        HandleInput(name="embedding", display_name="Embedding", input_types=["Embeddings"]),
        IntInput(
            name="number_of_results",
            display_name="Number of Results",
            info="Number of results to return.",
            advanced=True,
            value=4,
        ),
    ]

    @staticmethod
    def resolve_path(path: str) -> str:
        """解析并规范化持久化路径。

        契约：入参为相对或绝对路径，返回绝对路径字符串。
        失败语义：路径解析异常原样抛出。
        """
        return str(Path(path).resolve())

    def get_persist_directory(self) -> Path:
        """返回持久化目录路径，未设置则返回当前目录。

        契约：`persist_directory` 为空时返回 `Path()`。
        副作用：无。
        失败语义：无显式异常。
        """
        if self.persist_directory:
            return Path(self.resolve_path(self.persist_directory))
        return Path()

    @check_cached_vector_store
    def build_vector_store(self) -> FAISS:
        """构建并持久化 FAISS 索引。

        契约：`embedding` 必须可用，`ingest_data` 需可转为文档。
        副作用：创建目录并写入本地索引文件。
        失败语义：I/O 或 FAISS 构建失败时抛异常。
        关键路径（三步）：1) 准备数据 2) 构建索引 3) 本地保存。
        性能瓶颈：向量化与索引构建耗时。
        决策：使用本地磁盘持久化索引。
        问题：内存索引在重启后丢失。
        方案：构建后调用 `save_local` 写盘。
        代价：增加磁盘占用与写入耗时。
        重评：当需要远程索引或分布式存储时。
        """
        path = self.get_persist_directory()
        path.mkdir(parents=True, exist_ok=True)

        # 注意：统一转换为可被 FAISS 接受的文档对象。
        self.ingest_data = self._prepare_ingest_data()

        documents = []
        for _input in self.ingest_data or []:
            if isinstance(_input, Data):
                documents.append(_input.to_lc_document())
            else:
                documents.append(_input)

        faiss = FAISS.from_documents(documents=documents, embedding=self.embedding)
        faiss.save_local(str(path), self.index_name)
        return faiss

    def search_documents(self) -> list[Data]:
        """在 FAISS 索引中检索相似文档。

        契约：`search_query` 为空则返回空列表。
        副作用：可能触发索引构建或从磁盘加载索引。
        失败语义：索引加载失败抛 `ValueError`。
        关键路径（三步）：1) 定位索引 2) 构建或加载 3) 执行相似度搜索。
        异常流：索引文件缺失/损坏或反序列化失败。
        性能瓶颈：向量检索与磁盘 I/O。
        决策：优先复用本地索引，缺失则构建。
        问题：首次查询无索引可用。
        方案：检测索引文件是否存在，不存在则构建。
        代价：首查延迟增加。
        重评：当索引构建应在离线阶段完成时。
        """
        path = self.get_persist_directory()
        index_path = path / f"{self.index_name}.faiss"

        if not index_path.exists():
            vector_store = self.build_vector_store()
        else:
            vector_store = FAISS.load_local(
                folder_path=str(path),
                embeddings=self.embedding,
                index_name=self.index_name,
                allow_dangerous_deserialization=self.allow_dangerous_deserialization,
            )

        if not vector_store:
            msg = "Failed to load the FAISS index."
            raise ValueError(msg)

        if self.search_query and isinstance(self.search_query, str) and self.search_query.strip():
            docs = vector_store.similarity_search(
                query=self.search_query,
                k=self.number_of_results,
            )
            return docs_to_data(docs)
        return []
