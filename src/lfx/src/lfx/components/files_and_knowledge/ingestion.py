"""
模块名称：知识库写入组件

本模块提供知识库写入与更新流程，负责将 Data/DataFrame 向量化并持久化到本地知识库。
主要功能包括：
- 创建/选择知识库并校验名称
- 生成向量并写入 Chroma 存储
- 保存列配置与嵌入模型元数据

关键组件：
- KnowledgeIngestionComponent：知识库写入组件

设计背景：统一知识库入库流程，复用向量存储与加密元数据能力。
注意事项：Astra Cloud 环境不支持该组件；缺少 API Key 将导致嵌入失败。
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd
from cryptography.fernet import InvalidToken
from langchain_chroma import Chroma
from langflow.services.auth.utils import decrypt_api_key, encrypt_api_key
from langflow.services.database.models.user.crud import get_user_by_id

from lfx.base.knowledge_bases.knowledge_base_utils import get_knowledge_bases
from lfx.base.models.openai_constants import OPENAI_EMBEDDING_MODEL_NAMES
from lfx.components.processing.converter import convert_to_dataframe
from lfx.custom import Component
from lfx.io import (
    BoolInput,
    DropdownInput,
    HandleInput,
    IntInput,
    Output,
    SecretStrInput,
    StrInput,
    TableInput,
)
from lfx.schema.data import Data
from lfx.schema.table import EditMode
from lfx.services.deps import (
    get_settings_service,
    get_variable_service,
    session_scope,
)
from lfx.utils.validate_cloud import raise_error_if_astra_cloud_disable_component

if TYPE_CHECKING:
    from lfx.schema.dataframe import DataFrame

HUGGINGFACE_MODEL_NAMES = [
    "sentence-transformers/all-MiniLM-L6-v2",
    "sentence-transformers/all-mpnet-base-v2",
]
COHERE_MODEL_NAMES = ["embed-english-v3.0", "embed-multilingual-v3.0"]

_KNOWLEDGE_BASES_ROOT_PATH: Path | None = None

# 注意：Astra Cloud 环境不支持知识写入
astra_error_msg = "Knowledge ingestion is not supported in Astra cloud environment."


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


class KnowledgeIngestionComponent(Component):
    """知识库写入组件。

    契约：输入为 `Data`/`DataFrame`（已完成分块/处理）；需可用的嵌入模型。
    副作用：写入本地知识库目录与向量存储；更新元数据文件。
    失败语义：Astra Cloud 环境直接抛错；嵌入/存储错误会透传。
    """

    # ------ UI 元数据 ---------------------------------------------------
    display_name = "Knowledge Ingestion"
    description = "Create or update knowledge in Langflow."
    icon = "upload"
    name = "KnowledgeIngestion"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._cached_kb_path: Path | None = None

    @dataclass
    class NewKnowledgeBaseInput:
        functionality: str = "create"
        fields: dict[str, dict] = field(
            default_factory=lambda: {
                "data": {
                    "node": {
                        "name": "create_knowledge_base",
                        "description": "Create new knowledge in Langflow.",
                        "display_name": "Create new knowledge",
                        "field_order": [
                            "01_new_kb_name",
                            "02_embedding_model",
                            "03_api_key",
                        ],
                        "template": {
                            "01_new_kb_name": StrInput(
                                name="new_kb_name",
                                display_name="Knowledge Name",
                                info="Name of the new knowledge to create.",
                                required=True,
                            ),
                            "02_embedding_model": DropdownInput(
                                name="embedding_model",
                                display_name="Choose Embedding",
                                info="Select the embedding model to use for this knowledge base.",
                                required=True,
                                options=OPENAI_EMBEDDING_MODEL_NAMES + HUGGINGFACE_MODEL_NAMES + COHERE_MODEL_NAMES,
                                options_metadata=[{"icon": "OpenAI"} for _ in OPENAI_EMBEDDING_MODEL_NAMES]
                                + [{"icon": "HuggingFace"} for _ in HUGGINGFACE_MODEL_NAMES]
                                + [{"icon": "Cohere"} for _ in COHERE_MODEL_NAMES],
                            ),
                            "03_api_key": SecretStrInput(
                                name="api_key",
                                display_name="API Key",
                                info="Provider API key for embedding model",
                                required=True,
                                load_from_db=False,
                            ),
                        },
                    },
                }
            }
        )

    # ------ 输入 --------------------------------------------------------
    inputs = [
        DropdownInput(
            name="knowledge_base",
            display_name="Knowledge",
            info="Select the knowledge to load data from.",
            required=True,
            options=[],
            refresh_button=True,
            real_time_refresh=True,
            dialog_inputs=asdict(NewKnowledgeBaseInput()),
        ),
        HandleInput(
            name="input_df",
            display_name="Input",
            info=(
                "Table with all original columns (already chunked / processed). "
                "Accepts Data or DataFrame. If Data is provided, it is converted to a DataFrame automatically."
            ),
            input_types=["Data", "DataFrame"],
            required=True,
        ),
        TableInput(
            name="column_config",
            display_name="Column Configuration",
            info="Configure column behavior for the knowledge base.",
            required=True,
            table_schema=[
                {
                    "name": "column_name",
                    "display_name": "Column Name",
                    "type": "str",
                    "description": "Name of the column in the source DataFrame",
                    "edit_mode": EditMode.INLINE,
                },
                {
                    "name": "vectorize",
                    "display_name": "Vectorize",
                    "type": "boolean",
                    "description": "Create embeddings for this column",
                    "default": False,
                    "edit_mode": EditMode.INLINE,
                },
                {
                    "name": "identifier",
                    "display_name": "Identifier",
                    "type": "boolean",
                    "description": "Use this column as unique identifier",
                    "default": False,
                    "edit_mode": EditMode.INLINE,
                },
            ],
            value=[
                {
                    "column_name": "text",
                    "vectorize": True,
                    "identifier": True,
                },
            ],
        ),
        IntInput(
            name="chunk_size",
            display_name="Chunk Size",
            info="Batch size for processing embeddings",
            advanced=True,
            value=1000,
        ),
        SecretStrInput(
            name="api_key",
            display_name="Embedding Provider API Key",
            info="API key for the embedding provider to generate embeddings.",
            advanced=True,
            required=False,
        ),
        BoolInput(
            name="allow_duplicates",
            display_name="Allow Duplicates",
            info="Allow duplicate rows in the knowledge base",
            advanced=True,
            value=False,
        ),
    ]

    # ------ 输出 -------------------------------------------------------
    outputs = [Output(display_name="Results", name="dataframe_output", method="build_kb_info")]

    # ------ 内部辅助方法 ---------------------------------------------
    def _get_kb_root(self) -> Path:
        """返回知识库根目录路径。"""
        return _get_knowledge_bases_root_path()

    def _validate_column_config(self, df_source: pd.DataFrame) -> list[dict[str, Any]]:
        """校验列配置与数据源一致性。

        失败语义：列配置为空或列名不存在时抛 `ValueError`。
        """
        if not self.column_config:
            msg = "Column configuration cannot be empty"
            raise ValueError(msg)

        # 注意：TableInput 统一转换为 dict 列表
        config_list = self.column_config if isinstance(self.column_config, list) else []

        # 校验列名是否存在
        df_columns = set(df_source.columns)
        for config in config_list:
            col_name = config.get("column_name")
            if col_name not in df_columns:
                msg = f"Column '{col_name}' not found in DataFrame. Available columns: {sorted(df_columns)}"
                raise ValueError(msg)

        return config_list

    def _get_embedding_provider(self, embedding_model: str) -> str:
        """根据模型名称匹配嵌入提供方。"""
        if embedding_model in OPENAI_EMBEDDING_MODEL_NAMES:
            return "OpenAI"
        if embedding_model in HUGGINGFACE_MODEL_NAMES:
            return "HuggingFace"
        if embedding_model in COHERE_MODEL_NAMES:
            return "Cohere"
        return "Custom"

    def _build_embeddings(self, embedding_model: str, api_key: str):
        """构建嵌入模型实例。"""
        # 根据模型名匹配提供方
        provider = self._get_embedding_provider(embedding_model)

        # 校验提供方与密钥
        if provider == "OpenAI":
            from langchain_openai import OpenAIEmbeddings

            if not api_key:
                msg = "OpenAI API key is required when using OpenAI provider"
                raise ValueError(msg)
            return OpenAIEmbeddings(
                model=embedding_model,
                api_key=api_key,
                chunk_size=self.chunk_size,
            )
        if provider == "HuggingFace":
            from langchain_huggingface import HuggingFaceEmbeddings

            return HuggingFaceEmbeddings(
                model=embedding_model,
            )
        if provider == "Cohere":
            from langchain_cohere import CohereEmbeddings

            if not api_key:
                msg = "Cohere API key is required when using Cohere provider"
                raise ValueError(msg)
            return CohereEmbeddings(
                model=embedding_model,
                cohere_api_key=api_key,
            )
        if provider == "Custom":
            msg = "Custom embedding models not yet supported"
            raise NotImplementedError(msg)
        msg = f"Unknown provider: {provider}"
        raise ValueError(msg)

    def _build_embedding_metadata(self, embedding_model, api_key) -> dict[str, Any]:
        """构建并返回嵌入模型元数据。"""
        # 根据模型名匹配提供方
        embedding_provider = self._get_embedding_provider(embedding_model)

        api_key_to_save = None
        if api_key and hasattr(api_key, "get_secret_value"):
            api_key_to_save = api_key.get_secret_value()
        elif isinstance(api_key, str):
            api_key_to_save = api_key

        encrypted_api_key = None
        if api_key_to_save:
            settings_service = get_settings_service()
            try:
                encrypted_api_key = encrypt_api_key(api_key_to_save, settings_service=settings_service)
            except (TypeError, ValueError) as e:
                self.log(f"Could not encrypt API key: {e}")

        return {
            "embedding_provider": embedding_provider,
            "embedding_model": embedding_model,
            "api_key": encrypted_api_key,
            "api_key_used": bool(api_key),
            "chunk_size": self.chunk_size,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    def _save_embedding_metadata(self, kb_path: Path, embedding_model: str, api_key: str) -> None:
        """保存嵌入模型元数据到知识库目录。"""
        embedding_metadata = self._build_embedding_metadata(embedding_model, api_key)
        metadata_path = kb_path / "embedding_metadata.json"
        metadata_path.write_text(json.dumps(embedding_metadata, indent=2))

    def _save_kb_files(
        self,
        kb_path: Path,
        config_list: list[dict[str, Any]],
    ) -> None:
        """保存知识库配置文件。"""
        try:
            # 目录遵循 File 组件的存储结构
            kb_path.mkdir(parents=True, exist_ok=True)

            # 仅在首次创建时写入列配置
            cfg_path = kb_path / "schema.json"
            if not cfg_path.exists():
                cfg_path.write_text(json.dumps(config_list, indent=2))

        except (OSError, TypeError, ValueError) as e:
            self.log(f"Error saving KB files: {e}")

    def _build_column_metadata(self, config_list: list[dict[str, Any]], df_source: pd.DataFrame) -> dict[str, Any]:
        """构建列级元数据摘要。"""
        metadata: dict[str, Any] = {
            "total_columns": len(df_source.columns),
            "mapped_columns": len(config_list),
            "unmapped_columns": len(df_source.columns) - len(config_list),
            "columns": [],
            "summary": {"vectorized_columns": [], "identifier_columns": []},
        }

        for config in config_list:
            col_name = config.get("column_name")
            vectorize = config.get("vectorize") == "True" or config.get("vectorize") is True
            identifier = config.get("identifier") == "True" or config.get("identifier") is True

            metadata["columns"].append(
                {
                    "name": col_name,
                    "vectorize": vectorize,
                    "identifier": identifier,
                }
            )

            if vectorize:
                metadata["summary"]["vectorized_columns"].append(col_name)
            if identifier:
                metadata["summary"]["identifier_columns"].append(col_name)

        return metadata

    async def _create_vector_store(
        self,
        df_source: pd.DataFrame,
        config_list: list[dict[str, Any]],
        embedding_model: str,
        api_key: str,
    ) -> None:
        """创建向量存储并写入文档。"""
        try:
            # 初始化向量存储目录
            vector_store_dir = await self._kb_path()
            if not vector_store_dir:
                msg = "Knowledge base path is not set. Please create a new knowledge base first."
                raise ValueError(msg)
            vector_store_dir.mkdir(parents=True, exist_ok=True)

            # 创建嵌入模型
            embedding_function = self._build_embeddings(embedding_model, api_key)

            # 将 DataFrame 转为 Data 列表
            data_objects = await self._convert_df_to_data_objects(df_source, config_list)

            # 创建向量存储
            chroma = Chroma(
                persist_directory=str(vector_store_dir),
                embedding_function=embedding_function,
                collection_name=self.knowledge_base,
            )

            # 转为 LangChain 文档
            documents = []
            for data_obj in data_objects:
                doc = data_obj.to_lc_document()
                documents.append(doc)

            # 写入向量存储
            if documents:
                chroma.add_documents(documents)
                self.log(f"Added {len(documents)} documents to vector store '{self.knowledge_base}'")

        except (OSError, ValueError, RuntimeError) as e:
            self.log(f"Error creating vector store: {e}")

    async def _convert_df_to_data_objects(
        self, df_source: pd.DataFrame, config_list: list[dict[str, Any]]
    ) -> list[Data]:
        """将 DataFrame 转换为向量存储所需的 Data 列表。"""
        data_objects: list[Data] = []

        # 向量存储目录
        kb_path = await self._kb_path()

        # 若不允许重复，则读取已有哈希
        chroma = Chroma(
            persist_directory=str(kb_path),
            collection_name=self.knowledge_base,
        )

        # 获取已有文档元数据
        all_docs = chroma.get()

        # 提取已有 `_id`
        id_list = [metadata.get("_id") for metadata in all_docs["metadatas"] if metadata.get("_id")]

        # 按配置区分列角色
        content_cols = []
        identifier_cols = []

        for config in config_list:
            col_name = config.get("column_name")
            vectorize = config.get("vectorize") == "True" or config.get("vectorize") is True
            identifier = config.get("identifier") == "True" or config.get("identifier") is True

            if vectorize:
                content_cols.append(col_name)
            elif identifier:
                identifier_cols.append(col_name)

        # 逐行转换为 Data
        for _, row in df_source.iterrows():
            identifier_parts = [str(row[col]) for col in content_cols if col in row and pd.notna(row[col])]

            page_content = " ".join(identifier_parts)

            data_dict = {
                "text": page_content,
            }

            if identifier_cols:
                identifier_parts = [str(row[col]) for col in identifier_cols if col in row and pd.notna(row[col])]
                page_content = " ".join(identifier_parts)

            for col in df_source.columns:
                if col not in content_cols and col in row and pd.notna(row[col]):
                    value = row[col]
                    data_dict[col] = str(value)

            page_content_hash = hashlib.sha256(page_content.encode()).hexdigest()
            data_dict["_id"] = page_content_hash

            if not self.allow_duplicates and page_content_hash in id_list:
                self.log(f"Skipping duplicate row with hash {page_content_hash}")
                continue

            data_obj = Data(data=data_dict)
            data_objects.append(data_obj)

        return data_objects

    def is_valid_collection_name(self, name, min_length: int = 3, max_length: int = 63) -> bool:
        """校验集合名称是否合法。

        规则：
        1) 长度 3–63
        2) 首尾为字母或数字
        3) 仅包含字母数字、下划线或连字符
        """
        if not (min_length <= len(name) <= max_length):
            return False

        if not (name[0].isalnum() and name[-1].isalnum()):
            return False

        return re.match(r"^[a-zA-Z0-9_-]+$", name) is not None

    async def _kb_path(self) -> Path | None:
        # 优先使用缓存路径
        cached_path = getattr(self, "_cached_kb_path", None)
        if cached_path is not None:
            return cached_path

        # 无缓存则从用户信息推导
        async with session_scope() as db:
            if not self.user_id:
                msg = "User ID is required for fetching knowledge base path."
                raise ValueError(msg)
            current_user = await get_user_by_id(db, self.user_id)
            if not current_user:
                msg = f"User with ID {self.user_id} not found."
                raise ValueError(msg)
            kb_user = current_user.username

        kb_root = self._get_kb_root()

        # 缓存结果
        self._cached_kb_path = kb_root / kb_user / self.knowledge_base

        return self._cached_kb_path

    # ---------------------------------------------------------------------
    #                         输出方法
    # ---------------------------------------------------------------------
    async def build_kb_info(self) -> Data:
        """执行知识库写入并返回元数据。

        关键路径（三步）：
        1) 校验列配置并读取嵌入元数据。
        2) 构建向量存储并写入文档。
        3) 保存配置文件并返回元信息。

        异常流：Astra Cloud 环境禁用；嵌入/存储异常会抛 `RuntimeError`。
        """
        raise_error_if_astra_cloud_disable_component(astra_error_msg)
        try:
            input_value = self.input_df[0] if isinstance(self.input_df, list) else self.input_df
            df_source: DataFrame = convert_to_dataframe(input_value, auto_parse=False)

            # 校验列配置
            config_list = self._validate_column_config(df_source)
            column_metadata = self._build_column_metadata(config_list, df_source)

            # 读取已有嵌入元信息
            kb_path = await self._kb_path()
            if not kb_path:
                msg = "Knowledge base path is not set. Please create a new knowledge base first."
                raise ValueError(msg)
            metadata_path = kb_path / "embedding_metadata.json"

            # 未显式提供 API Key 时尝试从元数据读取
            if metadata_path.exists():
                settings_service = get_settings_service()
                metadata = json.loads(metadata_path.read_text())
                embedding_model = metadata.get("embedding_model")
                try:
                    api_key = decrypt_api_key(metadata["api_key"], settings_service)
                except (InvalidToken, TypeError, ValueError) as e:
                    self.log(f"Could not decrypt API key. Please provide it manually. Error: {e}")

            # 若提供自定义 API Key，则更新元数据
            if self.api_key:
                api_key = self.api_key
                self._save_embedding_metadata(
                    kb_path=kb_path,
                    embedding_model=embedding_model,
                    api_key=api_key,
                )

            # 创建向量存储并写入
            await self._create_vector_store(df_source, config_list, embedding_model=embedding_model, api_key=api_key)

            # 保存知识库文件
            self._save_kb_files(kb_path, config_list)

            # 构建返回元数据
            meta: dict[str, Any] = {
                "kb_id": str(uuid.uuid4()),
                "kb_name": self.knowledge_base,
                "rows": len(df_source),
                "column_metadata": column_metadata,
                "path": str(kb_path),
                "config_columns": len(config_list),
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            }

            # 更新状态
            self.status = f"✅ KB **{self.knowledge_base}** saved · {len(df_source)} chunks."

            return Data(data=meta)

        except (OSError, ValueError, RuntimeError, KeyError) as e:
            msg = f"Error during KB ingestion: {e}"
            raise RuntimeError(msg) from e

    async def _get_api_key_variable(self, field_value: dict[str, Any]):
        async with session_scope() as db:
            if not self.user_id:
                msg = "User ID is required for fetching global variables."
                raise ValueError(msg)
            current_user = await get_user_by_id(db, self.user_id)
            if not current_user:
                msg = f"User with ID {self.user_id} not found."
                raise ValueError(msg)
            variable_service = get_variable_service()

            # 处理 api_key 字段变量
            return await variable_service.get_variable(
                user_id=current_user.id,
                name=field_value["03_api_key"],
                field="",
                session=db,
            )

    async def update_build_config(
        self,
        build_config,
        field_value: Any,
        field_name: str | None = None,
    ):
        """根据选择更新构建配置。

        关键路径（三步）：
        1) 处理知识库创建请求并校验名称。
        2) 解析 API Key 并进行嵌入模型连通性验证。
        3) 刷新知识库列表与默认值。

        异常流：名称非法或 API Key 校验失败时抛 `ValueError`。
        """
        raise_error_if_astra_cloud_disable_component(astra_error_msg)
        # 处理知识库创建/选择
        if field_name == "knowledge_base":
            async with session_scope() as db:
                if not self.user_id:
                    msg = "User ID is required for fetching knowledge base list."
                    raise ValueError(msg)
                current_user = await get_user_by_id(db, self.user_id)
                if not current_user:
                    msg = f"User with ID {self.user_id} not found."
                    raise ValueError(msg)
            kb_user = current_user.username
            if isinstance(field_value, dict) and "01_new_kb_name" in field_value:
                # 校验知识库名称
                if not self.is_valid_collection_name(field_value["01_new_kb_name"]):
                    msg = f"Invalid knowledge base name: {field_value['01_new_kb_name']}"
                    raise ValueError(msg)

                api_key = field_value.get("03_api_key", None)
                with contextlib.suppress(Exception):
                    # 若 API Key 为变量则尝试解析
                    api_key = await self._get_api_key_variable(field_value)

                # 确保 api_key 为字符串
                if not isinstance(api_key, str):
                    msg = "API key must be a string."
                    raise ValueError(msg)

                # 使用嵌入模型进行一次连通性验证
                embed_model = self._build_embeddings(embedding_model=field_value["02_embedding_model"], api_key=api_key)

                # 生成测试向量以验证 API Key
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(embed_model.embed_query, "test"),
                        timeout=10,
                    )
                except TimeoutError as e:
                    msg = "Embedding validation timed out. Please verify network connectivity and key."
                    raise ValueError(msg) from e
                except Exception as e:
                    msg = f"Embedding validation failed: {e!s}"
                    raise ValueError(msg) from e

                # 创建知识库目录
                kb_path = _get_knowledge_bases_root_path() / kb_user / field_value["01_new_kb_name"]
                kb_path.mkdir(parents=True, exist_ok=True)

                # 保存嵌入元数据
                build_config["knowledge_base"]["value"] = field_value["01_new_kb_name"]
                self._save_embedding_metadata(
                    kb_path=kb_path,
                    embedding_model=field_value["02_embedding_model"],
                    api_key=api_key,
                )

            # 动态刷新知识库列表
            build_config["knowledge_base"]["options"] = await get_knowledge_bases(
                _get_knowledge_bases_root_path(),
                user_id=self.user_id,
            )

            # 若当前选择不可用则重置
            if build_config["knowledge_base"]["value"] not in build_config["knowledge_base"]["options"]:
                build_config["knowledge_base"]["value"] = None

        return build_config
