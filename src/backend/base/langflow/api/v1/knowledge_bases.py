"""
模块名称：知识库管理接口

本模块提供知识库的查询、统计与删除能力，面向前端的知识库列表与详情页。
主要功能：
- 探测知识库存储目录并提取元数据
- 统计知识库规模、分片与文本指标
- 单个/批量删除知识库目录
设计背景：统一管理本地持久化的向量知识库并提供可视化指标。
注意事项：目录删除为不可逆操作，异常统一转为 4xx/5xx。
"""

import json
import shutil
from http import HTTPStatus
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, HTTPException
from langchain_chroma import Chroma
from lfx.log import logger
from pydantic import BaseModel

from langflow.api.utils import CurrentActiveUser
from langflow.services.deps import get_settings_service

router = APIRouter(tags=["Knowledge Bases"], prefix="/knowledge_bases")


_KNOWLEDGE_BASES_DIR: Path | None = None


def _get_knowledge_bases_dir() -> Path:
    """从配置延迟加载知识库根目录。"""
    global _KNOWLEDGE_BASES_DIR  # noqa: PLW0603
    if _KNOWLEDGE_BASES_DIR is None:
        settings = get_settings_service().settings
        knowledge_directory = settings.knowledge_bases_dir
        if not knowledge_directory:
            msg = "Knowledge bases directory is not set in the settings."
            raise ValueError(msg)
        _KNOWLEDGE_BASES_DIR = Path(knowledge_directory).expanduser()
    return _KNOWLEDGE_BASES_DIR


class KnowledgeBaseInfo(BaseModel):
    """知识库摘要信息。"""

    id: str
    name: str
    embedding_provider: str | None = "Unknown"
    embedding_model: str | None = "Unknown"
    size: int = 0
    words: int = 0
    characters: int = 0
    chunks: int = 0
    avg_chunk_size: float = 0.0


class BulkDeleteRequest(BaseModel):
    """批量删除请求，包含知识库名称列表。"""

    kb_names: list[str]


def get_kb_root_path() -> Path:
    """返回知识库根目录路径。"""
    return _get_knowledge_bases_dir()


def get_directory_size(path: Path) -> int:
    """统计目录下所有文件的总大小（字节）。"""
    total_size = 0
    try:
        for file_path in path.rglob("*"):
            if file_path.is_file():
                total_size += file_path.stat().st_size
    except (OSError, PermissionError):
        pass
    return total_size


def detect_embedding_provider(kb_path: Path) -> str:
    """从配置文件与目录结构推断向量模型提供方。

    契约：
    - 输入：`kb_path`
    - 输出：提供方名称或 `"Unknown"`
    - 失败语义：配置文件读取失败会被记录并跳过

    关键路径（三步）：
    1) 遍历 JSON 配置查找显式提供方字段
    2) 通过关键词在配置文本中匹配提供方
    3) 以目录结构特征作为最终回退
    """
    # 实现：基于关键词匹配与目录特征进行识别。
    provider_patterns = {
        "OpenAI": ["openai", "text-embedding-ada", "text-embedding-3"],
        "HuggingFace": ["sentence-transformers", "huggingface", "bert-"],
        "Cohere": ["cohere", "embed-english", "embed-multilingual"],
        "Google": ["palm", "gecko", "google"],
        "Chroma": ["chroma"],
    }

    # 实现：优先扫描 JSON 配置，避免误判。
    for config_file in kb_path.glob("*.json"):
        try:
            with config_file.open("r", encoding="utf-8") as f:
                config_data = json.load(f)
                if not isinstance(config_data, dict):
                    continue

                config_str = json.dumps(config_data).lower()

                # 注意：先读取显式字段，可靠性更高。
                provider_fields = ["embedding_provider", "provider", "embedding_model_provider"]
                for field in provider_fields:
                    if field in config_data:
                        provider_value = str(config_data[field]).lower()
                        for provider, patterns in provider_patterns.items():
                            if any(pattern in provider_value for pattern in patterns):
                                return provider

                # 实现：回退到关键字匹配。
                for provider, patterns in provider_patterns.items():
                    if any(pattern in config_str for pattern in patterns):
                        return provider

        except (OSError, json.JSONDecodeError) as _:
            logger.exception("Error reading config file '%s'", config_file)
            continue

    # 实现：配置缺失时退回目录结构识别。
    if (kb_path / "chroma").exists():
        return "Chroma"
    if (kb_path / "vectors.npy").exists():
        return "Local"

    return "Unknown"


def detect_embedding_model(kb_path: Path) -> str:
    """从配置文件中识别向量模型名称。

    契约：
    - 输入：`kb_path`
    - 输出：模型名称或 `"Unknown"`
    - 失败语义：配置读取失败会被记录并继续

    关键路径（三步）：
    1) 读取 `embedding_metadata.json` 的显式模型字段
    2) 遍历其他 JSON 配置的显式模型字段
    3) 通过已知提供方模型关键词回退识别
    """
    # 注意：优先读取最可信的 `embedding_metadata.json`。
    metadata_file = kb_path / "embedding_metadata.json"
    if metadata_file.exists():
        try:
            with metadata_file.open("r", encoding="utf-8") as f:
                metadata = json.load(f)
                if isinstance(metadata, dict) and "embedding_model" in metadata:
                    # 实现：提取 `embedding_model` 字段。
                    model_value = str(metadata.get("embedding_model", "unknown"))
                    if model_value and model_value.lower() != "unknown":
                        return model_value
        except (OSError, json.JSONDecodeError) as _:
            logger.exception("Error reading embedding metadata file '%s'", metadata_file)

    # 实现：遍历其他 JSON 配置文件查找模型名。
    for config_file in kb_path.glob("*.json"):
        # 注意：跳过已处理的 metadata 文件。
        if config_file.name == "embedding_metadata.json":
            continue

        try:
            with config_file.open("r", encoding="utf-8") as f:
                config_data = json.load(f)
                if not isinstance(config_data, dict):
                    continue

                # 注意：优先读取显式模型字段。
                model_fields = ["embedding_model", "model", "embedding_model_name", "model_name"]
                for field in model_fields:
                    if field in config_data:
                        model_value = str(config_data[field])
                        if model_value and model_value.lower() != "unknown":
                            return model_value

                # 实现：识别 OpenAI 常见模型名。
                if "openai" in json.dumps(config_data).lower():
                    openai_models = ["text-embedding-ada-002", "text-embedding-3-small", "text-embedding-3-large"]
                    config_str = json.dumps(config_data).lower()
                    for model in openai_models:
                        if model in config_str:
                            return model

                # 实现：识别 HuggingFace 常见模型名。
                if "model" in config_data:
                    model_name = str(config_data["model"])
                    # 注意：匹配常见 HuggingFace 嵌入模型前缀。
                    hf_patterns = ["sentence-transformers", "all-MiniLM", "all-mpnet", "multi-qa"]
                    if any(pattern in model_name for pattern in hf_patterns):
                        return model_name

        except (OSError, json.JSONDecodeError) as _:
            logger.exception("Error reading config file '%s'", config_file)
            continue

    return "Unknown"


def get_text_columns(df: pd.DataFrame, schema_data: list | None = None) -> list[str]:
    """识别用于统计的文本列集合。"""
    # 实现：优先使用 schema 中标记为 `vectorize` 的字符串列。
    if schema_data:
        text_columns = [
            col["column_name"]
            for col in schema_data
            if col.get("vectorize", False) and col.get("data_type") == "string"
        ]
        if text_columns:
            return [col for col in text_columns if col in df.columns]

    # 实现：回退到常见列名匹配。
    common_names = ["text", "content", "document", "chunk"]
    text_columns = [col for col in df.columns if col.lower() in common_names]
    if text_columns:
        return text_columns

    # 实现：最后退回所有字符串列。
    return [col for col in df.columns if df[col].dtype == "object"]


def calculate_text_metrics(df: pd.DataFrame, text_columns: list[str]) -> tuple[int, int]:
    """计算文本列的总词数与字符数。"""
    total_words = 0
    total_characters = 0

    for col in text_columns:
        if col not in df.columns:
            continue

        text_series = df[col].astype(str).fillna("")
        total_characters += int(text_series.str.len().sum().item())
        total_words += int(text_series.str.split().str.len().sum().item())

    return total_words, total_characters


def get_kb_metadata(kb_path: Path) -> dict:
    """提取知识库目录的统计与模型元数据。

    契约：
    - 输入：`kb_path`
    - 输出：包含分片数、词数、字符数与模型信息的字典
    - 副作用：读取磁盘与 Chroma 数据库
    - 失败语义：异常会记录日志并返回已有字段

    关键路径（三步）：
    1) 读取 metadata/schema 文件并识别模型信息
    2) 打开 Chroma 集合并拉取文档元数据
    3) 统计文本指标并计算平均分片大小
    """
    metadata: dict[str, float | int | str] = {
        "chunks": 0,
        "words": 0,
        "characters": 0,
        "avg_chunk_size": 0.0,
        "embedding_provider": "Unknown",
        "embedding_model": "Unknown",
    }

    try:
        # 注意：优先读取 `embedding_metadata.json` 获取可靠的模型信息。
        metadata_file = kb_path / "embedding_metadata.json"
        if metadata_file.exists():
            try:
                with metadata_file.open("r", encoding="utf-8") as f:
                    embedding_metadata = json.load(f)
                    if isinstance(embedding_metadata, dict):
                        if "embedding_provider" in embedding_metadata:
                            metadata["embedding_provider"] = embedding_metadata["embedding_provider"]
                        if "embedding_model" in embedding_metadata:
                            metadata["embedding_model"] = embedding_metadata["embedding_model"]
            except (OSError, json.JSONDecodeError) as _:
                logger.exception("Error reading embedding metadata file '%s'", metadata_file)

        # 实现：元数据缺失时退回自动识别。
        if metadata["embedding_provider"] == "Unknown":
            metadata["embedding_provider"] = detect_embedding_provider(kb_path)
        if metadata["embedding_model"] == "Unknown":
            metadata["embedding_model"] = detect_embedding_model(kb_path)

        # 实现：读取 schema 获取文本列配置。
        schema_data = None
        schema_file = kb_path / "schema.json"
        if schema_file.exists():
            try:
                with schema_file.open("r", encoding="utf-8") as f:
                    schema_data = json.load(f)
                    if not isinstance(schema_data, list):
                        schema_data = None
            except (ValueError, TypeError, OSError) as _:
                logger.exception("Error reading schema file '%s'", schema_file)

        # 实现：打开 Chroma 向量库以读取源文档信息。
        chroma = Chroma(
            persist_directory=str(kb_path),
            collection_name=kb_path.name,
        )

        # 注意：访问底层集合以便读取原始文档与元数据。
        collection = chroma._collection  # noqa: SLF001

        # 实现：拉取文档与元数据。
        results = collection.get(include=["documents", "metadatas"])

        # 实现：转换为 DataFrame 便于统计。
        source_chunks = pd.DataFrame(
            {
                "document": results["documents"],
                "metadata": results["metadatas"],
            }
        )

        # 实现：统计分片数量与文本指标。
        try:
            metadata["chunks"] = len(source_chunks)

            # 实现：选取文本列并统计词数/字符数。
            text_columns = get_text_columns(source_chunks, schema_data)
            if text_columns:
                words, characters = calculate_text_metrics(source_chunks, text_columns)
                metadata["words"] = words
                metadata["characters"] = characters

                # 实现：计算平均分片大小。
                if int(metadata["chunks"]) > 0:
                    metadata["avg_chunk_size"] = round(int(characters) / int(metadata["chunks"]), 1)

        except (OSError, ValueError, TypeError) as _:
            logger.exception("Error processing Chroma DB '%s'", kb_path.name)

    except (OSError, ValueError, TypeError) as _:
        logger.exception("Error processing knowledge base directory '%s'", kb_path)

    return metadata


@router.get("", status_code=HTTPStatus.OK)
@router.get("/", status_code=HTTPStatus.OK)
async def list_knowledge_bases(current_user: CurrentActiveUser) -> list[KnowledgeBaseInfo]:
    """列出当前用户的全部知识库。

    契约：
    - 输入：`current_user`
    - 输出：`KnowledgeBaseInfo` 列表
    - 副作用：遍历文件系统并读取元数据
    - 失败语义：异常转 `HTTPException(500)`
    """
    try:
        kb_root_path = get_kb_root_path()
        kb_user = current_user.username
        kb_path = kb_root_path / kb_user

        if not kb_path.exists():
            return []

        knowledge_bases = []

        for kb_dir in kb_path.iterdir():
            if not kb_dir.is_dir() or kb_dir.name.startswith("."):
                continue

            try:
                # 实现：统计目录大小。
                size = get_directory_size(kb_dir)

                # 实现：读取知识库元数据。
                metadata = get_kb_metadata(kb_dir)

                kb_info = KnowledgeBaseInfo(
                    id=kb_dir.name,
                    name=kb_dir.name.replace("_", " ").replace("-", " ").title(),
                    embedding_provider=metadata["embedding_provider"],
                    embedding_model=metadata["embedding_model"],
                    size=size,
                    words=metadata["words"],
                    characters=metadata["characters"],
                    chunks=metadata["chunks"],
                    avg_chunk_size=metadata["avg_chunk_size"],
                )

                knowledge_bases.append(kb_info)

            except OSError as _:
                # 注意：无法读取的目录直接跳过。
                await logger.aexception("Error reading knowledge base directory '%s'", kb_dir)
                continue

        # 实现：按名称排序，确保前端稳定展示。
        knowledge_bases.sort(key=lambda x: x.name)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing knowledge bases: {e!s}") from e
    else:
        return knowledge_bases


@router.get("/{kb_name}", status_code=HTTPStatus.OK)
async def get_knowledge_base(kb_name: str, current_user: CurrentActiveUser) -> KnowledgeBaseInfo:
    """获取指定知识库的详细信息。"""
    try:
        kb_root_path = get_kb_root_path()
        kb_user = current_user.username
        kb_path = kb_root_path / kb_user / kb_name

        if not kb_path.exists() or not kb_path.is_dir():
            raise HTTPException(status_code=404, detail=f"Knowledge base '{kb_name}' not found")

        # 实现：统计目录大小与元数据。
        size = get_directory_size(kb_path)

        metadata = get_kb_metadata(kb_path)

        return KnowledgeBaseInfo(
            id=kb_name,
            name=kb_name.replace("_", " ").replace("-", " ").title(),
            embedding_provider=metadata["embedding_provider"],
            embedding_model=metadata["embedding_model"],
            size=size,
            words=metadata["words"],
            characters=metadata["characters"],
            chunks=metadata["chunks"],
            avg_chunk_size=metadata["avg_chunk_size"],
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting knowledge base '{kb_name}': {e!s}") from e


@router.delete("/{kb_name}", status_code=HTTPStatus.OK)
async def delete_knowledge_base(kb_name: str, current_user: CurrentActiveUser) -> dict[str, str]:
    """删除单个知识库目录。"""
    try:
        kb_root_path = get_kb_root_path()
        kb_user = current_user.username
        kb_path = kb_root_path / kb_user / kb_name

        if not kb_path.exists() or not kb_path.is_dir():
            raise HTTPException(status_code=404, detail=f"Knowledge base '{kb_name}' not found")

        # 注意：目录删除不可逆。
        shutil.rmtree(kb_path)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting knowledge base '{kb_name}': {e!s}") from e
    else:
        return {"message": f"Knowledge base '{kb_name}' deleted successfully"}


@router.delete("", status_code=HTTPStatus.OK)
@router.delete("/", status_code=HTTPStatus.OK)
async def delete_knowledge_bases_bulk(request: BulkDeleteRequest, current_user: CurrentActiveUser) -> dict[str, object]:
    """批量删除知识库目录。

    契约：
    - 输入：`BulkDeleteRequest.kb_names`
    - 输出：删除数量与未找到列表
    - 失败语义：全部未命中返回 404，部分失败继续处理并记录日志
    """
    try:
        kb_root_path = get_kb_root_path()
        kb_user = current_user.username
        kb_user_path = kb_root_path / kb_user
        deleted_count = 0
        not_found_kbs = []

        for kb_name in request.kb_names:
            kb_path = kb_user_path / kb_name

            if not kb_path.exists() or not kb_path.is_dir():
                not_found_kbs.append(kb_name)
                continue

            try:
                # 注意：目录删除不可逆。
                shutil.rmtree(kb_path)
                deleted_count += 1
            except (OSError, PermissionError) as e:
                await logger.aexception("Error deleting knowledge base '%s': %s", kb_name, e)
                # 注意：单个删除失败不影响其他任务。

        if not_found_kbs and deleted_count == 0:
            raise HTTPException(status_code=404, detail=f"Knowledge bases not found: {', '.join(not_found_kbs)}")

        result = {
            "message": f"Successfully deleted {deleted_count} knowledge base(s)",
            "deleted_count": deleted_count,
        }

        if not_found_kbs:
            result["not_found"] = ", ".join(not_found_kbs)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting knowledge bases: {e!s}") from e
    else:
        return result
