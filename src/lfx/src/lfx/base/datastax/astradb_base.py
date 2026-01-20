"""
模块名称：AstraDB 组件通用基类

本模块提供 Astra DB 相关组件的通用配置、元数据拉取与 UI 表单联动逻辑，主要用于 Langflow
中数据库与集合的选择/创建。主要功能包括：
- 生成数据库与集合创建对话框的输入模板
- 调用 Astra Data API 拉取数据库、集合与向量化提供方信息
- 维护 `build_config` 的联动与校验

关键组件：
- `AstraDBBaseComponent`：封装 Astra DB 交互与配置刷新逻辑
- `NewDatabaseInput`/`NewCollectionInput`：创建表单的结构模板

设计背景：SDK 组件需要统一 Astra DB 的交互与 UI 行为，避免重复实现
注意事项：网络调用失败时多以空列表/空字典降级，调用方需容忍空结果
"""

import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

from astrapy import DataAPIClient, Database
from langchain_astradb.utils.astradb import _AstraDBCollectionEnvironment

from lfx.custom.custom_component.component import Component
from lfx.io import (
    BoolInput,
    DropdownInput,
    IntInput,
    SecretStrInput,
    StrInput,
)
from lfx.log.logger import logger


class AstraDBBaseComponent(Component):
    """Astra DB 组件公共基类。

    契约：输入为组件字段与 `build_config`，输出为 Astra DB 元数据与联动后的配置。
    副作用：会触发 Astra Data API 网络请求与调试日志输出。
    失败语义：读取类接口多降级为空结果；创建类接口失败会抛 `ValueError`/`ImportError`。
    """

    @dataclass
    class NewDatabaseInput:
        """新建数据库对话框的输入模板定义。

        契约：输入为字段默认值，输出为 `asdict` 后的对话框模板结构。
        副作用：无。
        失败语义：无。
        """
        functionality: str = "create"
        fields: dict[str, dict] = field(
            default_factory=lambda: {
                "data": {
                    "node": {
                        "name": "create_database",
                        "description": "Please allow several minutes for creation to complete.",
                        "display_name": "Create new database",
                        "field_order": ["01_new_database_name", "02_cloud_provider", "03_region"],
                        "template": {
                            "01_new_database_name": StrInput(
                                name="new_database_name",
                                display_name="Name",
                                info="Name of the new database to create in Astra DB.",
                                required=True,
                            ),
                            "02_cloud_provider": DropdownInput(
                                name="cloud_provider",
                                display_name="Cloud provider",
                                info="Cloud provider for the new database.",
                                options=[],
                                required=True,
                                real_time_refresh=True,
                            ),
                            "03_region": DropdownInput(
                                name="region",
                                display_name="Region",
                                info="Region for the new database.",
                                options=[],
                                required=True,
                            ),
                        },
                    },
                }
            }
        )

    @dataclass
    class NewCollectionInput:
        """新建集合对话框的输入模板定义。

        契约：输入为字段默认值，输出为 `asdict` 后的对话框模板结构。
        副作用：无。
        失败语义：无。
        """
        functionality: str = "create"
        fields: dict[str, dict] = field(
            default_factory=lambda: {
                "data": {
                    "node": {
                        "name": "create_collection",
                        "description": "Please allow several seconds for creation to complete.",
                        "display_name": "Create new collection",
                        "field_order": [
                            "01_new_collection_name",
                            "02_embedding_generation_provider",
                            "03_embedding_generation_model",
                            "04_dimension",
                        ],
                        "template": {
                            "01_new_collection_name": StrInput(
                                name="new_collection_name",
                                display_name="Name",
                                info="Name of the new collection to create in Astra DB.",
                                required=True,
                            ),
                            "02_embedding_generation_provider": DropdownInput(
                                name="embedding_generation_provider",
                                display_name="Embedding generation method",
                                info="Provider to use for generating embeddings.",
                                helper_text=(
                                    "To create collections with more embedding provider options, go to "
                                    '<a class="underline" href="https://astra.datastax.com/" target=" _blank" '
                                    'rel="noopener noreferrer">your database in Astra DB</a>'
                                ),
                                real_time_refresh=True,
                                required=True,
                                options=[],
                            ),
                            "03_embedding_generation_model": DropdownInput(
                                name="embedding_generation_model",
                                display_name="Embedding model",
                                info="Model to use for generating embeddings.",
                                real_time_refresh=True,
                                options=[],
                            ),
                            "04_dimension": IntInput(
                                name="dimension",
                                display_name="Dimensions",
                                info="Dimensions of the embeddings to generate.",
                                value=None,
                            ),
                        },
                    },
                }
            }
        )

    inputs = [
        SecretStrInput(
            name="token",
            display_name="Astra DB Application Token",
            info="Authentication token for accessing Astra DB.",
            value="ASTRA_DB_APPLICATION_TOKEN",
            required=True,
            real_time_refresh=True,
            input_types=[],
        ),
        DropdownInput(
            name="environment",
            display_name="Environment",
            info="The environment for the Astra DB API Endpoint.",
            options=["prod", "test", "dev"],
            value="prod",
            advanced=True,
            real_time_refresh=True,
            combobox=True,
        ),
        DropdownInput(
            name="database_name",
            display_name="Database",
            info="The Database name for the Astra DB instance.",
            required=True,
            refresh_button=True,
            real_time_refresh=True,
            dialog_inputs=asdict(NewDatabaseInput()),
            combobox=True,
        ),
        DropdownInput(
            name="api_endpoint",
            display_name="Astra DB API Endpoint",
            info="The API Endpoint for the Astra DB instance. Supercedes database selection.",
            advanced=True,
        ),
        DropdownInput(
            name="keyspace",
            display_name="Keyspace",
            info="Optional keyspace within Astra DB to use for the collection.",
            advanced=True,
            options=[],
            real_time_refresh=True,
        ),
        DropdownInput(
            name="collection_name",
            display_name="Collection",
            info="The name of the collection within Astra DB where the vectors will be stored.",
            required=True,
            refresh_button=True,
            real_time_refresh=True,
            dialog_inputs=asdict(NewCollectionInput()),
            combobox=True,
            show=False,
        ),
        BoolInput(
            name="autodetect_collection",
            display_name="Autodetect Collection",
            info="Boolean flag to determine whether to autodetect the collection.",
            advanced=True,
            value=True,
        ),
    ]

    @classmethod
    def get_environment(cls, environment: str | None = None) -> str:
        """获取 Astra API 环境名。

        契约：输入 `environment`，为空时输出 `prod`，否则输出原值。
        副作用：无。
        失败语义：无。
        """
        if not environment:
            return "prod"
        return environment

    @classmethod
    def map_cloud_providers(cls, token: str, environment: str | None = None) -> dict[str, dict[str, Any]]:
        """拉取可用云厂商与区域映射。

        契约：输入 `token`/`environment`，输出 {展示名: {id, regions}}，仅包含 `AWS/GCP/Azure`。
        副作用：访问 Astra Data API 管理端，产生网络请求。
        失败语义：异常时记录 `Error fetching cloud providers` 并返回 `{}`。
        """
        try:
            client = DataAPIClient(environment=cls.get_environment(environment))
            admin_client = client.get_admin(token=token)

            available_regions = admin_client.find_available_regions(only_org_enabled_regions=True)

            provider_mapping: dict[str, dict[str, str]] = {
                "AWS": {"name": "Amazon Web Services", "id": "aws"},
                "GCP": {"name": "Google Cloud Platform", "id": "gcp"},
                "Azure": {"name": "Microsoft Azure", "id": "azure"},
            }

            result: dict[str, dict[str, Any]] = {}
            for region_info in available_regions:
                cloud_provider = region_info.cloud_provider
                region = region_info.name

                if cloud_provider in provider_mapping:
                    provider_name = provider_mapping[cloud_provider]["name"]
                    provider_id = provider_mapping[cloud_provider]["id"]

                    if provider_name not in result:
                        result[provider_name] = {"id": provider_id, "regions": []}

                    result[provider_name]["regions"].append(region)
        except Exception as e:  # noqa: BLE001
            logger.debug("Error fetching cloud providers: %s", e)
            return {}
        else:
            return result

    @classmethod
    def get_vectorize_providers(cls, token: str, environment: str | None = None, api_endpoint: str | None = None):
        """获取向量化提供方与模型列表。

        契约：输入 `token`/`environment`/`api_endpoint`，输出 {展示名: [provider_key, models]}。
        副作用：调用 Astra Data API 获取数据库向量化配置。
        失败语义：异常时返回 `{}`。
        """
        try:
            client = DataAPIClient(environment=cls.get_environment(environment))
            admin_client = client.get_admin()
            db_admin = admin_client.get_database_admin(api_endpoint, token=token)

            embedding_providers = db_admin.find_embedding_providers()

            vectorize_providers_mapping = {}
            for provider_key, provider_data in embedding_providers.embedding_providers.items():
                display_name = provider_data.display_name
                models = [model.name for model in provider_data.models]

                vectorize_providers_mapping[display_name] = [provider_key, models]

            return defaultdict(list, dict(sorted(vectorize_providers_mapping.items())))
        except Exception as _:  # noqa: BLE001
            return {}

    @classmethod
    async def create_database_api(
        cls,
        new_database_name: str,
        cloud_provider: str,
        region: str,
        token: str,
        environment: str | None = None,
        keyspace: str | None = None,
    ):
        """创建 Astra DB 数据库。

        契约：输入数据库名称与区域信息，输出创建任务对象。
        副作用：发起创建请求，`wait_until_active=False` 表示不等待激活完成。
        失败语义：`new_database_name` 为空抛 `ValueError`，API 异常向上抛出。
        """
        my_env = cls.get_environment(environment)

        client = DataAPIClient(environment=my_env)

        admin_client = client.get_admin(token=token)

        if not new_database_name:
            msg = "Database name is required to create a new database."
            raise ValueError(msg)

        return await admin_client.async_create_database(
            name=new_database_name,
            cloud_provider=cls.map_cloud_providers(token=token, environment=my_env)[cloud_provider]["id"],
            region=region,
            keyspace=keyspace,
            wait_until_active=False,
        )

    @classmethod
    async def create_collection_api(
        cls,
        new_collection_name: str,
        token: str,
        api_endpoint: str,
        environment: str | None = None,
        keyspace: str | None = None,
        dimension: int | None = None,
        embedding_generation_provider: str | None = None,
        embedding_generation_model: str | None = None,
    ):
        """创建集合并配置向量化选项。

        契约：输入集合名称与向量化参数，输出 `None`（仅触发创建）。
        副作用：调用 Astra Data API 创建集合。
        失败语义：`new_collection_name` 为空抛 `ValueError`；缺少 `langchain-astradb`
        时抛 `ImportError`。
        """
        vectorize_options = None
        if not dimension:
            try:
                from langchain_astradb import VectorServiceOptions
            except ImportError as e:
                msg = (
                    "langchain-astradb is required to create AstraDB collections with "
                    "Astra Vectorize embeddings. Please install it with "
                    "`pip install langchain-astradb`."
                )
                raise ImportError(msg) from e

            environment = cls.get_environment(environment)
            providers = cls.get_vectorize_providers(token=token, environment=environment, api_endpoint=api_endpoint)
            vectorize_options = VectorServiceOptions(
                provider=providers.get(embedding_generation_provider, [None, []])[0],
                model_name=embedding_generation_model,
            )

        if not new_collection_name:
            msg = "Collection name is required to create a new collection."
            raise ValueError(msg)

        base_args = {
            "collection_name": new_collection_name,
            "token": token,
            "api_endpoint": api_endpoint,
            "keyspace": keyspace,
            "environment": environment,
            "embedding_dimension": dimension,
            "collection_vector_service_options": vectorize_options,
        }

        _AstraDBCollectionEnvironment(**base_args)

    @classmethod
    def get_database_list_static(cls, token: str, environment: str | None = None):
        """获取数据库列表与元数据。

        契约：输入 `token`/`environment`，输出 {db_name: {api_endpoints,keyspaces,collections,status,org_id}}。
        副作用：访问 Astra Data API，并统计集合数量。
        失败语义：拉取失败返回 `{}`；单库统计失败且 `status=PENDING` 时集合数为 0。
        排障入口：日志关键字 `Error fetching database list`。
        """
        try:
            environment = cls.get_environment(environment)
            client = DataAPIClient(environment=environment)

            admin_client = client.get_admin(token=token)

            db_list = admin_client.list_databases()

            db_info_dict = {}
            for db in db_list:
                try:
                    api_endpoints = [db_reg.api_endpoint for db_reg in db.regions]

                    try:
                        num_collections = len(
                            client.get_database(
                                api_endpoints[0],
                                token=token,
                            ).list_collection_names()
                        )
                    except Exception:  # noqa: BLE001
                        if db.status != "PENDING":
                            continue
                        num_collections = 0

                    db_info_dict[db.name] = {
                        "api_endpoints": api_endpoints,
                        "keyspaces": db.keyspaces,
                        "collections": num_collections,
                        "status": db.status if db.status != "ACTIVE" else None,
                        "org_id": db.org_id if db.org_id else None,
                    }
                except Exception as e:  # noqa: BLE001
                    logger.debug("Failed to get metadata for database %s: %s", db.name, e)
        except Exception as e:  # noqa: BLE001
            logger.debug("Error fetching database list: %s", e)
            return {}
        else:
            return db_info_dict

    def get_database_list(self):
        """实例级数据库列表包装。

        契约：输入使用实例字段，输出数据库元数据字典。
        副作用：同 `get_database_list_static`。
        失败语义：同 `get_database_list_static`。
        """
        return self.get_database_list_static(
            token=self.token,
            environment=self.environment,
        )

    @classmethod
    def get_api_endpoint_static(
        cls,
        token: str,
        environment: str | None = None,
        api_endpoint: str | None = None,
        database_name: str | None = None,
    ):
        """解析数据库 API Endpoint。

        契约：输入 `token`/`environment`/`api_endpoint`/`database_name`，输出可用端点或 `None`。
        副作用：必要时调用 `get_database_list_static` 进行远程解析。
        失败语义：无法解析时返回 `None`。
        """
        if api_endpoint:
            return api_endpoint

        if database_name and database_name.startswith("https://"):
            return database_name

        if not database_name:
            return None

        environment = cls.get_environment(environment)
        db = cls.get_database_list_static(token=token, environment=environment).get(database_name)
        if not db:
            return None

        endpoints = db.get("api_endpoints") or []
        return endpoints[0] if endpoints else None

    def get_api_endpoint(self):
        """实例级 API Endpoint 解析包装。

        契约：输入使用实例字段，输出可用端点或 `None`。
        副作用：同 `get_api_endpoint_static`。
        失败语义：同 `get_api_endpoint_static`。
        """
        return self.get_api_endpoint_static(
            token=self.token,
            environment=self.environment,
            api_endpoint=self.api_endpoint,
            database_name=self.database_name,
        )

    @classmethod
    def get_database_id_static(cls, api_endpoint: str) -> str | None:
        """从 `api_endpoint` 提取数据库 UUID。

        契约：输入端点字符串，输出首个 UUID 或 `None`。
        副作用：无。
        失败语义：未命中返回 `None`。
        """
        uuid_pattern = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
        match = re.search(uuid_pattern, api_endpoint)

        return match.group(0) if match else None

    def get_database_id(self):
        """实例级数据库 UUID 提取包装。

        契约：输入使用实例字段，输出 UUID 或 `None`。
        副作用：可能触发远程解析 API Endpoint。
        失败语义：无端点或未命中时返回 `None`。
        """
        return self.get_database_id_static(api_endpoint=self.get_api_endpoint())

    def get_keyspace(self):
        """获取可用 keyspace。

        契约：输入实例 `keyspace`，输出去空格后的值或 `default_keyspace`。
        副作用：无。
        失败语义：无。
        """
        keyspace = self.keyspace

        if keyspace:
            return keyspace.strip()

        return "default_keyspace"

    def get_database_object(self, api_endpoint: str | None = None):
        """获取 `Database` 句柄。

        契约：输入 `api_endpoint`（可选），输出 Astra `Database` 对象。
        副作用：访问 Astra Data API，产生网络请求。
        失败语义：异常被包装为 `ValueError` 并包含原始错误信息。
        """
        try:
            client = DataAPIClient(environment=self.environment)

            return client.get_database(
                api_endpoint or self.get_api_endpoint(),
                token=self.token,
                keyspace=self.get_keyspace(),
            )
        except Exception as e:
            msg = f"Error fetching database object: {e}"
            raise ValueError(msg) from e

    def collection_data(self, collection_name: str, database: Database = None):
        """获取集合估算记录数。

        契约：输入集合名与可选 `database`，输出估算记录数或 `None`。
        副作用：可能创建新的 `Database` 连接并发起网络请求。
        失败语义：异常时通过 `self.log` 记录 `Error checking collection data`。
        """
        try:
            if not database:
                client = DataAPIClient(environment=self.environment)

                database = client.get_database(
                    self.get_api_endpoint(),
                    token=self.token,
                    keyspace=self.get_keyspace(),
                )

            collection = database.get_collection(collection_name)

            return collection.estimated_document_count()
        except Exception as e:  # noqa: BLE001
            self.log(f"Error checking collection data: {e}")

            return None

    def _initialize_database_options(self):
        """生成数据库下拉选项与元数据列表。

        契约：输入：无（使用实例字段）；输出包含 `name/status/collections/api_endpoints/keyspaces/org_id` 的列表。
        副作用：调用 `get_database_list` 触发远程拉取。
        失败语义：异常时记录调试日志并返回空列表。
        """
        try:
            db_list = self.get_database_list()
            if not db_list:
                return []
            return [
                {
                    "name": name,
                    "status": info["status"],
                    "collections": info["collections"],
                    "api_endpoints": info["api_endpoints"],
                    "keyspaces": info["keyspaces"],
                    "org_id": info["org_id"],
                }
                for name, info in db_list.items()
            ]
        except Exception as e:  # noqa: BLE001
            logger.debug("Error fetching database options: %s", e)
            return []

    @classmethod
    def get_provider_icon(cls, collection=None, provider_name: str | None = None) -> str:
        """解析向量化提供方的图标名。

        契约：输入 `collection` 或 `provider_name`，输出图标名字符串。
        副作用：无。
        失败语义：提供方缺失或为 `Bring your own` 时返回 `vectorstores`。
        """
        provider_name = provider_name or (
            collection.definition.vector.service.provider
            if (
                collection
                and collection.definition
                and collection.definition.vector
                and collection.definition.vector.service
            )
            else None
        )

        if not provider_name or provider_name.lower() == "bring your own":
            return "vectorstores"

        case_map = {
            "nvidia": "NVIDIA",
            "openai": "OpenAI",
            "amazon bedrock": "AmazonBedrockEmbeddings",
            "azure openai": "AzureOpenAiEmbeddings",
            "cohere": "Cohere",
            "jina ai": "JinaAI",
            "mistral ai": "MistralAI",
            "upstage": "Upstage",
            "voyage ai": "VoyageAI",
        }

        return case_map[provider_name.lower()] if provider_name.lower() in case_map else provider_name.title()

    def _initialize_collection_options(self, api_endpoint: str | None = None):
        """构建集合下拉选项与元数据。

        契约：输入 `api_endpoint`（可选），输出集合元数据列表。
        关键路径（三步）：
        1) 解析 `api_endpoint`，不足则返回空列表。
        2) 获取数据库对象并列出集合清单。
        3) 组合集合元数据（记录数/提供方/图标/模型）。

        异常流：`get_database_object` 失败将抛出 `ValueError`。
        性能瓶颈：`list_collections` 与逐个集合统计记录数的网络开销。
        排障入口：`Error fetching database object`。
        """
        api_endpoint = api_endpoint or self.get_api_endpoint()
        if not api_endpoint:
            return []

        database = self.get_database_object(api_endpoint=api_endpoint)

        collection_list = database.list_collections(keyspace=self.get_keyspace())

        return [
            {
                "name": col.name,
                "records": self.collection_data(collection_name=col.name, database=database),
                "provider": (
                    col.definition.vector.service.provider
                    if col.definition.vector and col.definition.vector.service
                    else None
                ),
                "icon": self.get_provider_icon(collection=col),
                "model": (
                    col.definition.vector.service.model_name
                    if col.definition.vector and col.definition.vector.service
                    else None
                ),
            }
            for col in collection_list
        ]

    def reset_provider_options(self, build_config: dict) -> dict:
        """重置向量化提供方选项与依赖字段。

        契约：输入 `build_config`，输出更新后的 `build_config`，仅保留 `Bring your own` 与 `nvidia`。
        副作用：原地修改 `build_config` 的模板与字段配置。
        失败语义：`build_config` 结构不完整时可能抛 `KeyError`。
        """
        template = build_config["collection_name"]["dialog_inputs"]["fields"]["data"]["node"]["template"]

        vectorize_providers_api = self.get_vectorize_providers(
            token=self.token,
            environment=self.environment,
            api_endpoint=build_config["api_endpoint"]["value"],
        )

        vectorize_providers: dict[str, list[list[str]]] = {"Bring your own": [[], []]}

        # 注意：当前仅保留 `nvidia`，扩展其他提供方需同步更新图标映射与模型列表。
        vectorize_providers.update(
            {
                k: v
                for k, v in vectorize_providers_api.items()
                if k.lower() in ["nvidia"]
            }
        )

        provider_field = "02_embedding_generation_provider"
        template[provider_field]["options"] = list(vectorize_providers.keys())

        template[provider_field]["options_metadata"] = [
            {"icon": self.get_provider_icon(provider_name=provider)} for provider in template[provider_field]["options"]
        ]

        embedding_provider = template[provider_field]["value"]
        is_bring_your_own = embedding_provider and embedding_provider == "Bring your own"

        model_field = "03_embedding_generation_model"
        template[model_field].update(
            {
                "options": vectorize_providers.get(embedding_provider, [[], []])[1],
                "placeholder": "Bring your own" if is_bring_your_own else None,
                "readonly": is_bring_your_own,
                "required": not is_bring_your_own,
                "value": None,
            }
        )

        return self.reset_dimension_field(build_config)

    def reset_dimension_field(self, build_config: dict) -> dict:
        """根据提供方状态重置维度字段。

        契约：输入 `build_config`，输出更新后的 `build_config`，非 `Bring your own` 时默认维度为 `1024`。
        副作用：原地更新维度字段的 `placeholder/value/readonly/required`。
        失败语义：字段结构缺失时可能抛 `KeyError`。
        """
        template = build_config["collection_name"]["dialog_inputs"]["fields"]["data"]["node"]["template"]

        provider_field = "02_embedding_generation_provider"
        embedding_provider = template[provider_field]["value"]
        is_bring_your_own = embedding_provider and embedding_provider == "Bring your own"

        dimension_field = "04_dimension"
        dimension_value = 1024 if not is_bring_your_own else None  # 注意：当前维度占位为固定值，未从模型动态读取。
        template[dimension_field].update(
            {
                "placeholder": dimension_value,
                "value": dimension_value,
                "readonly": not is_bring_your_own,
                "required": is_bring_your_own,
            }
        )

        return build_config

    def reset_collection_list(self, build_config: dict) -> dict:
        """重建集合下拉选项与元数据。

        契约：输入 `build_config`，输出更新后的 `build_config` 并同步集合选项。
        副作用：调用 `_initialize_collection_options` 触发远程拉取。
        失败语义：若远程失败，集合选项为空并保持现有选择清空。
        """
        collection_options = self._initialize_collection_options(api_endpoint=build_config["api_endpoint"]["value"])
        collection_config = build_config["collection_name"]
        collection_config.update(
            {
                "options": [col["name"] for col in collection_options],
                "options_metadata": [{k: v for k, v in col.items() if k != "name"} for col in collection_options],
            }
        )

        if collection_config["value"] not in collection_config["options"]:
            collection_config["value"] = ""

        collection_config["show"] = bool(build_config["database_name"]["value"])

        return build_config

    def reset_database_list(self, build_config: dict) -> dict:
        """重建数据库下拉选项并联动云厂商配置。

        契约：输入 `build_config`，输出更新后的 `build_config`，同步数据库与云厂商选项。
        副作用：调用 `map_cloud_providers` 与 `_initialize_database_options`。
        失败语义：远程失败时选项为空，`api_endpoint` 与集合选择被清空。
        """
        database_options = self._initialize_database_options()

        template = build_config["database_name"]["dialog_inputs"]["fields"]["data"]["node"]["template"]
        template["02_cloud_provider"]["options"] = list(
            self.map_cloud_providers(
                token=self.token,
                environment=self.environment,
            ).keys()
        )

        database_config = build_config["database_name"]
        database_config.update(
            {
                "options": [db["name"] for db in database_options],
                "options_metadata": [{k: v for k, v in db.items() if k != "name"} for db in database_options],
            }
        )

        if database_config["value"] not in database_config["options"]:
            database_config["value"] = ""
            build_config["api_endpoint"]["options"] = []
            build_config["api_endpoint"]["value"] = ""
            build_config["collection_name"]["show"] = False

        database_config["show"] = bool(build_config["token"]["value"])

        return build_config

    def reset_build_config(self, build_config: dict) -> dict:
        """清空全部构建配置选项。

        契约：输入 `build_config`，输出清空后的 `build_config`。
        副作用：原地修改 `build_config`。
        失败语义：无。
        """
        database_config = build_config["database_name"]
        database_config.update({"options": [], "options_metadata": [], "value": "", "show": False})
        build_config["api_endpoint"]["options"] = []
        build_config["api_endpoint"]["value"] = ""

        collection_config = build_config["collection_name"]
        collection_config.update({"options": [], "options_metadata": [], "value": "", "show": False})

        return build_config

    async def update_build_config(
        self,
        build_config: dict,
        field_value: str | dict,
        field_name: str | None = None,
    ) -> dict:
        """根据字段变化联动刷新 `build_config`。

        契约：输入 `build_config/field_value/field_name`，输出更新后的 `build_config`。
        关键路径（三步）：
        1) 无 `token` 时清空配置并直接返回。
        2) 处理新建数据库/集合与提供方联动逻辑。
        3) 处理选择变更并刷新数据库/集合列表。

        异常流：创建数据库/集合失败会抛 `ValueError`。
        性能瓶颈：依赖数据库/集合拉取的网络调用。
        排障入口：异常消息前缀 `Error creating`。
        """
        if not self.token:
            return self.reset_build_config(build_config)

        if field_name == "database_name" and isinstance(field_value, dict):
            if "01_new_database_name" in field_value:
                await self._create_new_database(build_config, field_value)
                return self.reset_collection_list(build_config)
            return self._update_cloud_regions(build_config, field_value)

        if field_name == "collection_name" and isinstance(field_value, dict):
            if "01_new_collection_name" in field_value:
                await self._create_new_collection(build_config, field_value)
                return build_config

            if "02_embedding_generation_provider" in field_value:
                return self.reset_provider_options(build_config)

            if "03_embedding_generation_model" in field_value:
                return self.reset_dimension_field(build_config)

        first_run = field_name == "collection_name" and not field_value and not build_config["database_name"]["options"]
        if first_run or field_name in {"token", "environment"}:
            return self.reset_database_list(build_config)

        if field_name == "database_name" and not isinstance(field_value, dict):
            return self._handle_database_selection(build_config, field_value)

        if field_name == "keyspace":
            return self.reset_collection_list(build_config)

        if field_name == "collection_name" and not isinstance(field_value, dict):
            return self._handle_collection_selection(build_config, field_value)

        return build_config

    async def _create_new_database(self, build_config: dict, field_value: dict) -> None:
        """创建数据库并追加到本地选项。

        契约：输入 `build_config/field_value`，输出 `None`，并追加 `PENDING` 元数据。
        副作用：调用 Astra Data API 创建数据库。
        失败语义：异常被包装为 `ValueError` 并向上抛出。
        """
        try:
            await self.create_database_api(
                new_database_name=field_value["01_new_database_name"],
                token=self.token,
                keyspace=self.get_keyspace(),
                environment=self.environment,
                cloud_provider=field_value["02_cloud_provider"],
                region=field_value["03_region"],
            )
        except Exception as e:
            msg = f"Error creating database: {e}"
            raise ValueError(msg) from e

        build_config["database_name"]["options"].append(field_value["01_new_database_name"])
        build_config["database_name"]["options_metadata"].append(
            {
                "status": "PENDING",
                "collections": 0,
                "api_endpoints": [],
                "keyspaces": [self.get_keyspace()],
                "org_id": None,
            }
        )

    def _update_cloud_regions(self, build_config: dict, field_value: dict) -> dict:
        """根据云厂商选择刷新区域选项。

        契约：输入 `build_config/field_value`，输出更新后的 `build_config`。
        副作用：调用 `map_cloud_providers` 触发远程拉取。
        失败语义：结构缺失时可能抛 `KeyError`。
        """
        cloud_provider = field_value["02_cloud_provider"]

        template = build_config["database_name"]["dialog_inputs"]["fields"]["data"]["node"]["template"]
        template["03_region"]["options"] = self.map_cloud_providers(
            token=self.token,
            environment=self.environment,
        )[cloud_provider]["regions"]

        if template["03_region"]["value"] not in template["03_region"]["options"]:
            template["03_region"]["value"] = None

        return build_config

    async def _create_new_collection(self, build_config: dict, field_value: dict) -> None:
        """创建集合并同步元数据。

        契约：输入 `build_config/field_value`，输出 `None`，并补充集合元数据。
        副作用：调用 Astra Data API 创建集合。
        失败语义：异常被包装为 `ValueError` 并向上抛出。
        """
        embedding_provider = field_value.get("02_embedding_generation_provider")
        try:
            await self.create_collection_api(
                new_collection_name=field_value["01_new_collection_name"],
                token=self.token,
                api_endpoint=build_config["api_endpoint"]["value"],
                environment=self.environment,
                keyspace=self.get_keyspace(),
                dimension=field_value.get("04_dimension") if embedding_provider == "Bring your own" else None,
                embedding_generation_provider=embedding_provider,
                embedding_generation_model=field_value.get("03_embedding_generation_model"),
            )
        except Exception as e:
            msg = f"Error creating collection: {e}"
            raise ValueError(msg) from e

        provider = embedding_provider.lower() if embedding_provider and embedding_provider != "Bring your own" else None
        build_config["collection_name"].update(
            {
                "value": field_value["01_new_collection_name"],
                "options": build_config["collection_name"]["options"] + [field_value["01_new_collection_name"]],
            }
        )

        build_config["collection_name"]["options_metadata"].append(
            {
                "records": 0,
                "provider": provider,
                "icon": self.get_provider_icon(provider_name=provider),
                "model": field_value.get("03_embedding_generation_model"),
            }
        )

    def _handle_database_selection(self, build_config: dict, field_value: str) -> dict:
        """响应数据库选择并联动刷新依赖项。

        契约：输入 `build_config/field_value`，输出更新后的 `build_config`。
        关键路径（三步）：
        1) 重置数据库列表并校验选中值。
        2) 回填 `api_endpoint`/`keyspace` 与帮助链接。
        3) 刷新提供方与集合列表。

        异常流：缺失 `org_id` 时仅返回基础配置。
        性能瓶颈：数据库列表刷新与集合列表拉取。
        排障入口：`Error fetching database list`。
        """
        build_config = self.reset_database_list(build_config)

        if field_value not in build_config["database_name"]["options"]:
            build_config["database_name"]["value"] = ""
            return build_config

        index = build_config["database_name"]["options"].index(field_value)
        build_config["api_endpoint"]["options"] = build_config["database_name"]["options_metadata"][index][
            "api_endpoints"
        ]
        build_config["api_endpoint"]["value"] = build_config["database_name"]["options_metadata"][index][
            "api_endpoints"
        ][0]

        org_id = build_config["database_name"]["options_metadata"][index]["org_id"]
        if not org_id:
            return build_config

        build_config["keyspace"]["options"] = build_config["database_name"]["options_metadata"][index]["keyspaces"]
        build_config["keyspace"]["value"] = (
            build_config["keyspace"]["options"] and build_config["keyspace"]["options"][0]
            if build_config["keyspace"]["value"] not in build_config["keyspace"]["options"]
            else build_config["keyspace"]["value"]
        )

        db_id = self.get_database_id_static(api_endpoint=build_config["api_endpoint"]["value"])
        keyspace = self.get_keyspace()

        template = build_config["collection_name"]["dialog_inputs"]["fields"]["data"]["node"]["template"]
        template["02_embedding_generation_provider"]["helper_text"] = (
            "To create collections with more embedding provider options, go to "
            f'<a class="underline" target="_blank" rel="noopener noreferrer" '
            f'href="https://astra.datastax.com/org/{org_id}/database/{db_id}/data-explorer?createCollection=1&namespace={keyspace}">'
            "your database in Astra DB</a>."
        )

        build_config = self.reset_provider_options(build_config)

        return self.reset_collection_list(build_config)

    def _handle_collection_selection(self, build_config: dict, field_value: str) -> dict:
        """响应集合选择并联动向量化选项。

        契约：输入 `build_config/field_value`，输出更新后的 `build_config`。
        关键路径（三步）：
        1) 重置集合列表并开启自动探测。
        2) 将未识别集合加入选项并标记为非自动。
        3) 返回更新后的 `build_config`。

        异常流：无显式异常分支，错误由上游结构决定。
        性能瓶颈：集合列表重建时的远程调用。
        排障入口：`Error checking collection data`。
        """
        build_config["autodetect_collection"]["value"] = True
        build_config = self.reset_collection_list(build_config)

        if field_value and field_value not in build_config["collection_name"]["options"]:
            build_config["collection_name"]["options"].append(field_value)
            build_config["collection_name"]["options_metadata"].append(
                {
                    "records": 0,
                    "provider": None,
                    "icon": "vectorstores",
                    "model": None,
                }
            )
            build_config["autodetect_collection"]["value"] = False

        return build_config
