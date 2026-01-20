"""
模块名称：知识库检索组件

本模块提供知识库检索能力，读取 Chroma 向量存储并返回检索结果。
主要功能包括：
- 读取嵌入元数据并构建嵌入器
- 支持相似度检索并可选返回评分/向量
- 将结果统一封装为 `DataFrame`

关键组件：
- KnowledgeRetrievalComponent：知识库检索组件

设计背景：统一检索流程并复用嵌入配置，避免用户重复配置。
注意事项：Astra Cloud 环境不支持该组件；缺少嵌入元数据会抛错。
"""

import json
from pathlib import Path
from typing import Any

from cryptography.fernet import InvalidToken
from langchain_chroma import Chroma
from langflow.services.auth.utils import decrypt_api_key
from langflow.services.database.models.user.crud import get_user_by_id
from pydantic import SecretStr

from lfx.base.knowledge_bases.knowledge_base_utils import get_knowledge_bases
from lfx.custom import Component
from lfx.io import BoolInput, DropdownInput, IntInput, MessageTextInput, Output, SecretStrInput
from lfx.log.logger import logger
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame
from lfx.services.deps import get_settings_service, session_scope
from lfx.utils.validate_cloud import raise_error_if_astra_cloud_disable_component

_KNOWLEDGE_BASES_ROOT_PATH: Path | None = None

# 注意：Astra Cloud 环境不支持知识检索
astra_error_msg = "Knowledge retrieval is not supported in Astra cloud environment."


def _get_knowledge_bases_root_path() -> Path:
    """从配置中延迟加载知识库根目录。

    失败语义：未配置知识库目录时抛 `ValueError`。
    """
    global _KNOWLEDGE_BASES_ROOT_PATH  # noqa: PLW0603
    if _KNOWLEDGE_BASES_ROOT_PATH is None:
        settings = get_settings_service().settings
        knowledge_directory = settings.knowledge_bases_dir
        if not knowledge_directory:
            msg = "Knowledge bases directory is not set in the settings."
            raise ValueError(msg)
        _KNOWLEDGE_BASES_ROOT_PATH = Path(knowledge_directory).expanduser()
    return _KNOWLEDGE_BASES_ROOT_PATH


class KnowledgeRetrievalComponent(Component):
    """知识库检索组件。

    契约：必须选择 `knowledge_base`；可选提供 `api_key` 覆盖元数据。
    副作用：读取本地知识库与向量存储；产生日志。
    失败语义：Astra Cloud 环境直接抛错；缺失元数据/鉴权失败会抛异常。
    """

    display_name = "Knowledge Retrieval"
    description = "Search and retrieve data from knowledge."
    icon = "download"
    name = "KnowledgeRetrieval"

    inputs = [
        DropdownInput(
            name="knowledge_base",
            display_name="Knowledge",
            info="Select the knowledge to load data from.",
            required=True,
            options=[],
            refresh_button=True,
            real_time_refresh=True,
        ),
        SecretStrInput(
            name="api_key",
            display_name="Embedding Provider API Key",
            info="API key for the embedding provider to generate embeddings.",
            advanced=True,
            required=False,
        ),
        MessageTextInput(
            name="search_query",
            display_name="Search Query",
            info="Optional search query to filter knowledge base data.",
            tool_mode=True,
        ),
        IntInput(
            name="top_k",
            display_name="Top K Results",
            info="Number of top results to return from the knowledge base.",
            value=5,
            advanced=True,
            required=False,
        ),
        BoolInput(
            name="include_metadata",
            display_name="Include Metadata",
            info="Whether to include all metadata in the output. If false, only content is returned.",
            value=True,
            advanced=False,
        ),
        BoolInput(
            name="include_embeddings",
            display_name="Include Embeddings",
            info="Whether to include embeddings in the output. Only applicable if 'Include Metadata' is enabled.",
            value=False,
            advanced=True,
        ),
    ]

    outputs = [
        Output(
            name="retrieve_data",
            display_name="Results",
            method="retrieve_data",
            info="Returns the data from the selected knowledge base.",
        ),
    ]

    async def update_build_config(self, build_config, field_value, field_name=None):  # noqa: ARG002
        """根据选择更新构建配置。"""
        raise_error_if_astra_cloud_disable_component(astra_error_msg)
        if field_name == "knowledge_base":
            # 动态刷新知识库列表
            build_config["knowledge_base"]["options"] = await get_knowledge_bases(
                _get_knowledge_bases_root_path(),
                user_id=self.user_id,  # 使用组件上下文中的 user_id
            )

            # 若当前选择不可用则重置
            if build_config["knowledge_base"]["value"] not in build_config["knowledge_base"]["options"]:
                build_config["knowledge_base"]["value"] = None

        return build_config

    def _get_kb_metadata(self, kb_path: Path) -> dict:
        """读取并处理知识库元数据。"""
        raise_error_if_astra_cloud_disable_component(astra_error_msg)
        metadata: dict[str, Any] = {}
        metadata_file = kb_path / "embedding_metadata.json"
        if not metadata_file.exists():
            logger.warning(f"Embedding metadata file not found at {metadata_file}")
            return metadata

        try:
            with metadata_file.open("r", encoding="utf-8") as f:
                metadata = json.load(f)
        except json.JSONDecodeError:
            logger.error(f"Error decoding JSON from {metadata_file}")
            return {}

        # 解密 API Key（若存在）
        if "api_key" in metadata and metadata.get("api_key"):
            settings_service = get_settings_service()
            try:
                decrypted_key = decrypt_api_key(metadata["api_key"], settings_service)
                metadata["api_key"] = decrypted_key
            except (InvalidToken, TypeError, ValueError) as e:
                logger.error(f"Could not decrypt API key. Please provide it manually. Error: {e}")
                metadata["api_key"] = None
        return metadata

    def _build_embeddings(self, metadata: dict):
        """根据元数据构建嵌入模型。"""
        runtime_api_key = self.api_key.get_secret_value() if isinstance(self.api_key, SecretStr) else self.api_key
        provider = metadata.get("embedding_provider")
        model = metadata.get("embedding_model")
        api_key = runtime_api_key or metadata.get("api_key")
        chunk_size = metadata.get("chunk_size")

        # 根据提供方构建嵌入器
        if provider == "OpenAI":
            from langchain_openai import OpenAIEmbeddings

            if not api_key:
                msg = "OpenAI API key is required. Provide it in the component's advanced settings."
                raise ValueError(msg)
            return OpenAIEmbeddings(
                model=model,
                api_key=api_key,
                chunk_size=chunk_size,
            )
        if provider == "HuggingFace":
            from langchain_huggingface import HuggingFaceEmbeddings

            return HuggingFaceEmbeddings(
                model=model,
            )
        if provider == "Cohere":
            from langchain_cohere import CohereEmbeddings

            if not api_key:
                msg = "Cohere API key is required when using Cohere provider"
                raise ValueError(msg)
            return CohereEmbeddings(
                model=model,
                cohere_api_key=api_key,
            )
        if provider == "Custom":
            msg = "Custom embedding models not yet supported"
            raise NotImplementedError(msg)
        msg = f"Embedding provider '{provider}' is not supported for retrieval."
        raise NotImplementedError(msg)

    async def retrieve_data(self) -> DataFrame:
        """从知识库检索数据并返回 `DataFrame`。

        关键路径（三步）：
        1) 根据用户与知识库定位存储路径。
        2) 构建嵌入器并执行相似度检索。
        3) 组装结果与可选元数据/嵌入输出。

        异常流：缺失元数据或鉴权失败时抛 `ValueError`。
        """
        raise_error_if_astra_cloud_disable_component(astra_error_msg)
        # 获取当前用户以定位知识库路径
        async with session_scope() as db:
            if not self.user_id:
                msg = "User ID is required for fetching Knowledge Base data."
                raise ValueError(msg)
            current_user = await get_user_by_id(db, self.user_id)
            if not current_user:
                msg = f"User with ID {self.user_id} not found."
                raise ValueError(msg)
            kb_user = current_user.username
        kb_path = _get_knowledge_bases_root_path() / kb_user / self.knowledge_base

        metadata = self._get_kb_metadata(kb_path)
        if not metadata:
            msg = f"Metadata not found for knowledge base: {self.knowledge_base}. Ensure it has been indexed."
            raise ValueError(msg)

        # 构建嵌入器
        embedding_function = self._build_embeddings(metadata)

        # 加载向量存储
        chroma = Chroma(
            persist_directory=str(kb_path),
            embedding_function=embedding_function,
            collection_name=self.knowledge_base,
        )

        # 若提供查询语句则执行相似度检索
        if self.search_query:
            logger.info(f"Performing similarity search with query: {self.search_query}")
            results = chroma.similarity_search_with_score(
                query=self.search_query or "",
                k=self.top_k,
            )
        else:
            results = chroma.similarity_search(
                query=self.search_query or "",
                k=self.top_k,
            )

            # 补齐评分字段以保持输出结构一致
            results = [(doc, 0) for doc in results]  # 使用占位评分以保持结构一致

        # 如需嵌入向量则批量拉取
        id_to_embedding = {}
        if self.include_embeddings and results:
            doc_ids = [doc[0].metadata.get("_id") for doc in results if doc[0].metadata.get("_id")]

            if doc_ids:
                collection = chroma._collection  # noqa: SLF001
                embeddings_result = collection.get(where={"_id": {"$in": doc_ids}}, include=["metadatas", "embeddings"])

                for i, metadata in enumerate(embeddings_result.get("metadatas", [])):
                    if metadata and "_id" in metadata:
                        id_to_embedding[metadata["_id"]] = embeddings_result["embeddings"][i]

        # 根据配置组装输出
        data_list = []
        for doc in results:
            kwargs = {
                "content": doc[0].page_content,
            }
            if self.search_query:
                kwargs["_score"] = -1 * doc[1]
            if self.include_metadata:
                kwargs.update(doc[0].metadata)
            if self.include_embeddings:
                kwargs["_embeddings"] = id_to_embedding.get(doc[0].metadata.get("_id"))

            data_list.append(Data(**kwargs))

        return DataFrame(data=data_list)
