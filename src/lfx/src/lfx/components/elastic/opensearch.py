"""
模块名称：OpenSearch 向量检索组件

本模块提供 OpenSearch 向量存储与混合检索组件，支持向量相似度与关键词联合搜索，
并提供认证、过滤与聚合能力。主要功能包括：
- 构建 OpenSearch 客户端与索引映射
- 批量写入文档与向量
- 混合检索（KNN + 关键字）与过滤

关键组件：
- `OpenSearchVectorStoreComponent`：OpenSearch 组件入口

设计背景：为 OpenSearch 场景提供统一向量检索组件封装。
注意事项：Amazon OpenSearch Serverless 对向量引擎有限制，需校验兼容性。
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from opensearchpy import OpenSearch, helpers

from lfx.base.vectorstores.model import LCVectorStoreComponent, check_cached_vector_store
from lfx.base.vectorstores.vector_store_connection_decorator import vector_store_connection
from lfx.io import BoolInput, DropdownInput, HandleInput, IntInput, MultilineInput, SecretStrInput, StrInput, TableInput
from lfx.log import logger
from lfx.schema.data import Data


@vector_store_connection
class OpenSearchVectorStoreComponent(LCVectorStoreComponent):
    """OpenSearch 向量存储组件（混合检索）。

    契约：输入连接参数、嵌入器与过滤配置，输出可检索的 OpenSearch 客户端结果。
    副作用：可能创建索引、写入文档并执行搜索请求。
    失败语义：认证参数缺失或引擎不兼容会抛 `ValueError`。
    """

    display_name: str = "OpenSearch"
    icon: str = "OpenSearch"
    description: str = (
        "Store and search documents using OpenSearch with hybrid semantic and keyword search capabilities."
    )

    default_keys: list[str] = [
        "opensearch_url",
        "index_name",
        *[i.name for i in LCVectorStoreComponent.inputs],
        "embedding",
        "vector_field",
        "number_of_results",
        "auth_mode",
        "username",
        "password",
        "jwt_token",
        "jwt_header",
        "bearer_prefix",
        "use_ssl",
        "verify_certs",
        "filter_expression",
        "engine",
        "space_type",
        "ef_construction",
        "m",
        "docs_metadata",
    ]

    inputs = [
        TableInput(
            name="docs_metadata",
            display_name="Document Metadata",
            info=(
                "Additional metadata key-value pairs to be added to all ingested documents. "
                "Useful for tagging documents with source information, categories, or other custom attributes."
            ),
            table_schema=[
                {
                    "name": "key",
                    "display_name": "Key",
                    "type": "str",
                    "description": "Key name",
                },
                {
                    "name": "value",
                    "display_name": "Value",
                    "type": "str",
                    "description": "Value of the metadata",
                },
            ],
            value=[],
            input_types=["Data"],
        ),
        StrInput(
            name="opensearch_url",
            display_name="OpenSearch URL",
            value="http://localhost:9200",
            info=(
                "The connection URL for your OpenSearch cluster "
                "(e.g., http://localhost:9200 for local development or your cloud endpoint)."
            ),
        ),
        StrInput(
            name="index_name",
            display_name="Index Name",
            value="langflow",
            info=(
                "The OpenSearch index name where documents will be stored and searched. "
                "Will be created automatically if it doesn't exist."
            ),
        ),
        DropdownInput(
            name="engine",
            display_name="Vector Engine",
            options=["jvector", "nmslib", "faiss", "lucene"],
            value="jvector",
            info=(
                "Vector search engine for similarity calculations. 'jvector' is recommended for most use cases. "
                "Note: Amazon OpenSearch Serverless only supports 'nmslib' or 'faiss'."
            ),
            advanced=True,
        ),
        DropdownInput(
            name="space_type",
            display_name="Distance Metric",
            options=["l2", "l1", "cosinesimil", "linf", "innerproduct"],
            value="l2",
            info=(
                "Distance metric for calculating vector similarity. 'l2' (Euclidean) is most common, "
                "'cosinesimil' for cosine similarity, 'innerproduct' for dot product."
            ),
            advanced=True,
        ),
        IntInput(
            name="ef_construction",
            display_name="EF Construction",
            value=512,
            info=(
                "Size of the dynamic candidate list during index construction. "
                "Higher values improve recall but increase indexing time and memory usage."
            ),
            advanced=True,
        ),
        IntInput(
            name="m",
            display_name="M Parameter",
            value=16,
            info=(
                "Number of bidirectional connections for each vector in the HNSW graph. "
                "Higher values improve search quality but increase memory usage and indexing time."
            ),
            advanced=True,
        ),
        *LCVectorStoreComponent.inputs,
        HandleInput(name="embedding", display_name="Embedding", input_types=["Embeddings"]),
        StrInput(
            name="vector_field",
            display_name="Vector Field Name",
            value="chunk_embedding",
            advanced=True,
            info="Name of the field in OpenSearch documents that stores the vector embeddings for similarity search.",
        ),
        IntInput(
            name="number_of_results",
            display_name="Default Result Limit",
            value=10,
            advanced=True,
            info=(
                "Default maximum number of search results to return when no limit is "
                "specified in the filter expression."
            ),
        ),
        MultilineInput(
            name="filter_expression",
            display_name="Search Filters (JSON)",
            value="",
            info=(
                "Optional JSON configuration for search filtering, result limits, and score thresholds.\n\n"
                "Format 1 - Explicit filters:\n"
                '{"filter": [{"term": {"filename":"doc.pdf"}}, '
                '{"terms":{"owner":["user1","user2"]}}], "limit": 10, "score_threshold": 1.6}\n\n'
                "Format 2 - Context-style mapping:\n"
                '{"data_sources":["file.pdf"], "document_types":["application/pdf"], "owners":["user123"]}\n\n'
                "Use __IMPOSSIBLE_VALUE__ as placeholder to ignore specific filters."
            ),
        ),
        DropdownInput(
            name="auth_mode",
            display_name="Authentication Mode",
            value="basic",
            options=["basic", "jwt"],
            info=(
                "Authentication method: 'basic' for username/password authentication, "
                "or 'jwt' for JSON Web Token (Bearer) authentication."
            ),
            real_time_refresh=True,
            advanced=False,
        ),
        StrInput(
            name="username",
            display_name="Username",
            value="admin",
            show=False,
        ),
        SecretStrInput(
            name="password",
            display_name="OpenSearch Password",
            value="admin",
            show=False,
        ),
        SecretStrInput(
            name="jwt_token",
            display_name="JWT Token",
            value="JWT",
            load_from_db=False,
            show=True,
            info=(
                "Valid JSON Web Token for authentication. "
                "Will be sent in the Authorization header (with optional 'Bearer ' prefix)."
            ),
        ),
        StrInput(
            name="jwt_header",
            display_name="JWT Header Name",
            value="Authorization",
            show=False,
            advanced=True,
        ),
        BoolInput(
            name="bearer_prefix",
            display_name="Prefix 'Bearer '",
            value=True,
            show=False,
            advanced=True,
        ),
        BoolInput(
            name="use_ssl",
            display_name="Use SSL/TLS",
            value=True,
            advanced=True,
            info="Enable SSL/TLS encryption for secure connections to OpenSearch.",
        ),
        BoolInput(
            name="verify_certs",
            display_name="Verify SSL Certificates",
            value=False,
            advanced=True,
            info=(
                "Verify SSL certificates when connecting. "
                "Disable for self-signed certificates in development environments."
            ),
        ),
    ]

    def _default_text_mapping(
        self,
        dim: int,
        engine: str = "jvector",
        space_type: str = "l2",
        ef_search: int = 512,
        ef_construction: int = 100,
        m: int = 16,
        vector_field: str = "vector_field",
    ) -> dict[str, Any]:
        """生成默认向量索引映射。

        契约：输入向量维度与引擎配置，输出 OpenSearch mapping 字典。
        副作用：无。
        失败语义：无。
        """
        return {
            "settings": {"index": {"knn": True, "knn.algo_param.ef_search": ef_search}},
            "mappings": {
                "properties": {
                    vector_field: {
                        "type": "knn_vector",
                        "dimension": dim,
                        "method": {
                            "name": "disk_ann",
                            "space_type": space_type,
                            "engine": engine,
                            "parameters": {"ef_construction": ef_construction, "m": m},
                        },
                    }
                }
            },
        }

    def _validate_aoss_with_engines(self, *, is_aoss: bool, engine: str) -> None:
        """校验 AOSS 与向量引擎兼容性。

        契约：输入是否为 AOSS 与引擎类型，校验通过则无返回。
        副作用：无。
        失败语义：不兼容时抛 `ValueError`。
        """
        if is_aoss and engine not in {"nmslib", "faiss"}:
            msg = "Amazon OpenSearch Service Serverless only supports `nmslib` or `faiss` engines"
            raise ValueError(msg)

    def _is_aoss_enabled(self, http_auth: Any) -> bool:
        """判断是否为 Amazon OpenSearch Serverless (AOSS)。

        契约：输入认证对象，输出布尔值。
        副作用：无。
        失败语义：无。
        """
        return http_auth is not None and hasattr(http_auth, "service") and http_auth.service == "aoss"

    def _bulk_ingest_embeddings(
        self,
        client: OpenSearch,
        index_name: str,
        embeddings: list[list[float]],
        texts: list[str],
        metadatas: list[dict] | None = None,
        ids: list[str] | None = None,
        vector_field: str = "vector_field",
        text_field: str = "text",
        mapping: dict | None = None,
        max_chunk_bytes: int | None = 1 * 1024 * 1024,
        *,
        is_aoss: bool = False,
    ) -> list[str]:
        """批量写入向量文档。

        契约：输入向量、文本与元数据，输出成功写入的文档 ID 列表。
        副作用：调用 OpenSearch bulk 写入。
        失败语义：异常由底层客户端抛出。
        """
        if not mapping:
            mapping = {}

        requests = []
        return_ids = []

        for i, text in enumerate(texts):
            metadata = metadatas[i] if metadatas else {}
            _id = ids[i] if ids else str(uuid.uuid4())
            request = {
                "_op_type": "index",
                "_index": index_name,
                vector_field: embeddings[i],
                text_field: text,
                **metadata,
            }
            if is_aoss:
                request["id"] = _id
            else:
                request["_id"] = _id
            requests.append(request)
            return_ids.append(_id)
        if metadatas:
            self.log(f"Sample metadata: {metadatas[0] if metadatas else {}}")
        helpers.bulk(client, requests, max_chunk_bytes=max_chunk_bytes)
        return return_ids

    def _build_auth_kwargs(self) -> dict[str, Any]:
        """构建 OpenSearch 认证参数。

        契约：根据 `auth_mode` 返回认证配置字典。
        副作用：无。
        失败语义：认证信息缺失时抛 `ValueError`。
        """
        mode = (self.auth_mode or "basic").strip().lower()
        if mode == "jwt":
            token = (self.jwt_token or "").strip()
            if not token:
                msg = "Auth Mode is 'jwt' but no jwt_token was provided."
                raise ValueError(msg)
            header_name = (self.jwt_header or "Authorization").strip()
            header_value = f"Bearer {token}" if self.bearer_prefix else token
            return {"headers": {header_name: header_value}}
        user = (self.username or "").strip()
        pwd = (self.password or "").strip()
        if not user or not pwd:
            msg = "Auth Mode is 'basic' but username/password are missing."
            raise ValueError(msg)
        return {"http_auth": (user, pwd)}

    def build_client(self) -> OpenSearch:
        """创建 OpenSearch 客户端实例。

        契约：输出已配置的 `OpenSearch` 客户端。
        副作用：无（仅构造对象）。
        失败语义：认证配置异常将向上抛出。
        """
        auth_kwargs = self._build_auth_kwargs()
        return OpenSearch(
            hosts=[self.opensearch_url],
            use_ssl=self.use_ssl,
            verify_certs=self.verify_certs,
            ssl_assert_hostname=False,
            ssl_show_warn=False,
            **auth_kwargs,
        )

    @check_cached_vector_store
    def build_vector_store(self) -> OpenSearch:
        """构建向量存储客户端并写入文档。

        契约：输出 OpenSearch 客户端（作为向量存储使用）。
        副作用：会触发文档写入。
        失败语义：写入异常将向上抛出。
        """
        self.log(self.ingest_data)
        client = self.build_client()
        self._add_documents_to_vector_store(client=client)
        return client

    def _add_documents_to_vector_store(self, client: OpenSearch) -> None:
        """处理并写入文档。

        契约：输入 OpenSearch 客户端，写入当前 `ingest_data`。
        关键路径（三步）：
        1) 预处理文档与元数据。
        2) 生成向量并校验 AOSS/引擎兼容性。
        3) 批量写入并记录结果。

        异常流：缺少嵌入器或写入失败会抛 `ValueError`。
        """
        self.ingest_data = self._prepare_ingest_data()

        docs = self.ingest_data or []
        if not docs:
            self.log("No documents to ingest.")
            return

        texts = []
        metadatas = []
        additional_metadata = {}
        if hasattr(self, "docs_metadata") and self.docs_metadata:
            logger.debug(f"[LF] Docs metadata {self.docs_metadata}")
            if isinstance(self.docs_metadata[-1], Data):
                logger.debug(f"[LF] Docs metadata is a Data object {self.docs_metadata}")
                self.docs_metadata = self.docs_metadata[-1].data
                logger.debug(f"[LF] Docs metadata is a Data object {self.docs_metadata}")
                additional_metadata.update(self.docs_metadata)
            else:
                for item in self.docs_metadata:
                    if isinstance(item, dict) and "key" in item and "value" in item:
                        additional_metadata[item["key"]] = item["value"]
        for key, value in additional_metadata.items():
            if value == "None":
                additional_metadata[key] = None
        logger.debug(f"[LF] Additional metadata {additional_metadata}")
        for doc_obj in docs:
            data_copy = json.loads(doc_obj.model_dump_json())
            text = data_copy.pop(doc_obj.text_key, doc_obj.default_value)
            texts.append(text)

            data_copy.update(additional_metadata)

            metadatas.append(data_copy)
        self.log(metadatas)
        if not self.embedding:
            msg = "Embedding handle is required to embed documents."
            raise ValueError(msg)

        vectors = self.embedding.embed_documents(texts)

        if not vectors:
            self.log("No vectors generated from documents.")
            return

        dim = len(vectors[0]) if vectors else 768

        auth_kwargs = self._build_auth_kwargs()
        is_aoss = self._is_aoss_enabled(auth_kwargs.get("http_auth"))

        engine = getattr(self, "engine", "jvector")
        self._validate_aoss_with_engines(is_aoss=is_aoss, engine=engine)

        space_type = getattr(self, "space_type", "l2")
        ef_construction = getattr(self, "ef_construction", 512)
        m = getattr(self, "m", 16)

        mapping = self._default_text_mapping(
            dim=dim,
            engine=engine,
            space_type=space_type,
            ef_construction=ef_construction,
            m=m,
            vector_field=self.vector_field,
        )

        self.log(f"Indexing {len(texts)} documents into '{self.index_name}' with proper KNN mapping...")

        return_ids = self._bulk_ingest_embeddings(
            client=client,
            index_name=self.index_name,
            embeddings=vectors,
            texts=texts,
            metadatas=metadatas,
            vector_field=self.vector_field,
            text_field="text",
            mapping=mapping,
            is_aoss=is_aoss,
        )
        self.log(metadatas)

        self.log(f"Successfully indexed {len(return_ids)} documents.")

    def _is_placeholder_term(self, term_obj: dict) -> bool:
        """判断是否为占位过滤条件。"""
        return any(v == "__IMPOSSIBLE_VALUE__" for v in term_obj.values())

    def _coerce_filter_clauses(self, filter_obj: dict | None) -> list[dict]:
        """将过滤配置转换为 OpenSearch 过滤子句。

        契约：输入过滤对象（支持两种格式），输出标准化 filter 子句列表。
        副作用：无。
        失败语义：无效 JSON 时返回空列表。
        """
        if not filter_obj:
            return []

        if isinstance(filter_obj, str):
            try:
                filter_obj = json.loads(filter_obj)
            except json.JSONDecodeError:
                return []

        if "filter" in filter_obj:
            raw = filter_obj["filter"]
            if isinstance(raw, dict):
                raw = [raw]
            explicit_clauses: list[dict] = []
            for f in raw or []:
                if "term" in f and isinstance(f["term"], dict) and not self._is_placeholder_term(f["term"]):
                    explicit_clauses.append(f)
                elif "terms" in f and isinstance(f["terms"], dict):
                    field, vals = next(iter(f["terms"].items()))
                    if isinstance(vals, list) and len(vals) > 0:
                        explicit_clauses.append(f)
            return explicit_clauses

        field_mapping = {
            "data_sources": "filename",
            "document_types": "mimetype",
            "owners": "owner",
        }
        context_clauses: list[dict] = []
        for k, values in filter_obj.items():
            if not isinstance(values, list):
                continue
            field = field_mapping.get(k, k)
            if len(values) == 0:
                context_clauses.append({"term": {field: "__IMPOSSIBLE_VALUE__"}})
            elif len(values) == 1:
                if values[0] != "__IMPOSSIBLE_VALUE__":
                    context_clauses.append({"term": {field: values[0]}})
            else:
                context_clauses.append({"terms": {field: values}})
        return context_clauses

    def search(self, query: str | None = None) -> list[dict[str, Any]]:
        """执行混合检索（向量 + 关键词）。

        契约：输入查询文本，输出包含内容/元数据/分数的结果列表。
        关键路径（三步）：
        1) 解析过滤条件并生成查询向量。
        2) 组合 KNN 与关键词检索请求。
        3) 返回命中结果并附带分数。

        异常流：缺少嵌入器或过滤 JSON 非法时抛 `ValueError`。
        """
        logger.info(self.ingest_data)
        client = self.build_client()
        q = (query or "").strip()

        filter_obj = None
        if getattr(self, "filter_expression", "") and self.filter_expression.strip():
            try:
                filter_obj = json.loads(self.filter_expression)
            except json.JSONDecodeError as e:
                msg = f"Invalid filter_expression JSON: {e}"
                raise ValueError(msg) from e

        if not self.embedding:
            msg = "Embedding is required to run hybrid search (KNN + keyword)."
            raise ValueError(msg)

        vec = self.embedding.embed_query(q)

        filter_clauses = self._coerce_filter_clauses(filter_obj)

        limit = (filter_obj or {}).get("limit", self.number_of_results)
        score_threshold = (filter_obj or {}).get("score_threshold", 0)

        body = {
            "query": {
                "bool": {
                    "should": [
                        {
                            "knn": {
                                self.vector_field: {
                                    "vector": vec,
                                    "k": 10,
                                    "boost": 0.7,
                                }
                            }
                        },
                        {
                            "multi_match": {
                                "query": q,
                                "fields": ["text^2", "filename^1.5"],
                                "type": "best_fields",
                                "fuzziness": "AUTO",
                                "boost": 0.3,
                            }
                        },
                    ],
                    "minimum_should_match": 1,
                }
            },
            "aggs": {
                "data_sources": {"terms": {"field": "filename", "size": 20}},
                "document_types": {"terms": {"field": "mimetype", "size": 10}},
                "owners": {"terms": {"field": "owner", "size": 10}},
            },
            "_source": [
                "filename",
                "mimetype",
                "page",
                "text",
                "source_url",
                "owner",
                "allowed_users",
                "allowed_groups",
            ],
            "size": limit,
        }
        if filter_clauses:
            body["query"]["bool"]["filter"] = filter_clauses

        if isinstance(score_threshold, (int, float)) and score_threshold > 0:
            body["min_score"] = score_threshold

        resp = client.search(index=self.index_name, body=body)
        hits = resp.get("hits", {}).get("hits", [])
        return [
            {
                "page_content": hit["_source"].get("text", ""),
                "metadata": {k: v for k, v in hit["_source"].items() if k != "text"},
                "score": hit.get("_score"),
            }
            for hit in hits
        ]

    def search_documents(self) -> list[Data]:
        """执行检索并返回 `Data` 列表。

        契约：使用 `search_query` 执行检索并输出 `Data`。
        副作用：可能触发网络请求与日志记录。
        失败语义：检索异常向上抛出。
        """
        try:
            raw = self.search(self.search_query or "")
            return [Data(text=hit["page_content"], **hit["metadata"]) for hit in raw]
            self.log(self.ingest_data)
        except Exception as e:
            self.log(f"search_documents error: {e}")
            raise

    async def update_build_config(self, build_config: dict, field_value: str, field_name: str | None = None) -> dict:
        """根据字段变化动态调整配置。

        契约：输入当前配置与变更字段，输出更新后的配置。
        副作用：修改字段可见性与必填状态。
        失败语义：字段缺失时记录日志并返回原配置。
        """
        try:
            if field_name == "auth_mode":
                mode = (field_value or "basic").strip().lower()
                is_basic = mode == "basic"
                is_jwt = mode == "jwt"

                build_config["username"]["show"] = is_basic
                build_config["password"]["show"] = is_basic

                build_config["jwt_token"]["show"] = is_jwt
                build_config["jwt_header"]["show"] = is_jwt
                build_config["bearer_prefix"]["show"] = is_jwt

                build_config["username"]["required"] = is_basic
                build_config["password"]["required"] = is_basic

                build_config["jwt_token"]["required"] = is_jwt
                build_config["jwt_header"]["required"] = is_jwt
                build_config["bearer_prefix"]["required"] = False

                if is_basic:
                    build_config["jwt_token"]["value"] = ""

                return build_config

        except (KeyError, ValueError) as e:
            self.log(f"update_build_config error: {e}")

        return build_config
