"""模块名称：本地向量库（Chroma）组件

本模块封装 Chroma 本地向量库的构建、入库与检索逻辑，支持 Ingest/Retrieve 两种模式。
主要功能包括：构建持久化目录、动态切换配置项、写入向量与执行相似检索。

关键组件：
- `LocalDBComponent`：本地向量库组件入口

设计背景：在本地文件系统模式下提供轻量持久化向量库。
注意事项：云存储模式（S3/Astra）禁用本地向量库；需依赖 `langchain-chroma`。
"""

from copy import deepcopy
from pathlib import Path

from langchain_chroma import Chroma
from typing_extensions import override

from lfx.base.vectorstores.model import LCVectorStoreComponent, check_cached_vector_store
from lfx.base.vectorstores.utils import chroma_collection_to_data
from lfx.inputs.inputs import MultilineInput
from lfx.io import BoolInput, DropdownInput, HandleInput, IntInput, MessageTextInput, TabInput
from lfx.log.logger import logger
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame
from lfx.template.field.base import Output
from lfx.utils.validate_cloud import raise_error_if_astra_cloud_disable_component

disable_component_in_astra_cloud_msg = (
    "Local vector stores are not supported in S3/cloud mode. "
    "Local vector stores require local file system access for persistence. "
    "Please use cloud-based vector stores (Pinecone, Weaviate, etc.) or local storage mode."
)


class LocalDBComponent(LCVectorStoreComponent):
    """本地 Chroma 向量库组件。

    契约：输入 `embedding/ingest_data/search_query` 等；输出 `DataFrame`；
    副作用：创建本地持久化目录、写入向量库、更新 `self.status`；
    失败语义：缺少依赖抛 `ImportError`，云模式禁用抛异常。
    关键路径：1) 构建/复用集合目录 2) 构建 Chroma 实例 3) 入库或检索。
    决策：使用本地目录持久化
    问题：需要离线/本地可复现的向量存储
    方案：将集合写入 `{base}/vector_stores/{collection_name}`
    代价：依赖本地文件系统，云环境不可用
    重评：当统一云向量库存储时移除本地实现
    """

    display_name: str = "Local DB"
    description: str = "Local Vector Store with search capabilities"
    name = "LocalDB"
    icon = "database"
    legacy = True

    inputs = [
        TabInput(
            name="mode",
            display_name="Mode",
            options=["Ingest", "Retrieve"],
            info="Select the operation mode",
            value="Ingest",
            real_time_refresh=True,
            show=True,
        ),
        MessageTextInput(
            name="collection_name",
            display_name="Collection Name",
            value="langflow",
            required=True,
        ),
        MessageTextInput(
            name="persist_directory",
            display_name="Persist Directory",
            info=(
                "Custom base directory to save the vector store. "
                "Collections will be stored under '{directory}/vector_stores/{collection_name}'. "
                "If not specified, it will use your system's cache folder."
            ),
            advanced=True,
        ),
        DropdownInput(
            name="existing_collections",
            display_name="Existing Collections",
            options=[],  # 运行时动态填充
            info="Select a previously created collection to search through its stored data.",
            show=False,
            combobox=True,
        ),
        HandleInput(name="embedding", display_name="Embedding", required=True, input_types=["Embeddings"]),
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
        HandleInput(
            name="ingest_data",
            display_name="Ingest Data",
            input_types=["Data", "DataFrame"],
            is_list=True,
            info="Data to store. It will be embedded and indexed for semantic search.",
            show=True,
        ),
        MultilineInput(
            name="search_query",
            display_name="Search Query",
            tool_mode=True,
            info="Enter text to search for similar content in the selected collection.",
            show=False,
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
    outputs = [
        Output(display_name="DataFrame", name="dataframe", method="perform_search"),
    ]

    def get_vector_store_directory(self, base_dir: str | Path) -> Path:
        """获取集合对应的持久化目录。

        契约：输入 `base_dir`；输出集合目录 `Path`；副作用：确保目录存在；
        失败语义：目录创建失败会抛异常。
        关键路径：1) 规范化为 `Path` 2) 拼接集合目录 3) 创建目录。
        决策：强制创建目录
        问题：向量库需要持久化路径
        方案：`mkdir(parents=True, exist_ok=True)`
        代价：可能创建空目录
        重评：当需要只读模式时改为不创建
        """
        base_dir = Path(base_dir)
        full_path = base_dir / "vector_stores" / self.collection_name
        full_path.mkdir(parents=True, exist_ok=True)
        return full_path

    def get_default_persist_dir(self) -> str:
        """获取默认持久化目录。

        契约：输入无；输出默认目录字符串；副作用：可能创建目录；
        失败语义：目录创建失败会抛异常。
        关键路径：1) 读取缓存目录 2) 生成集合目录。
        决策：默认使用缓存目录
        问题：无需显式配置即可持久化
        方案：复用 `CACHE_DIR`
        代价：缓存目录清理可能删除集合
        重评：当需要持久化策略配置时开放入口
        """
        from lfx.services.cache.utils import CACHE_DIR

        return str(self.get_vector_store_directory(CACHE_DIR))

    def list_existing_collections(self) -> list[str]:
        """列出已有集合名称。

        契约：输入无；输出集合名列表；副作用无；
        失败语义：目录不存在时返回空列表。
        关键路径：1) 确定基目录 2) 扫描 `vector_stores` 3) 返回目录名。
        决策：仅遍历目录名
        问题：集合以目录形式存储
        方案：过滤 `is_dir()`
        代价：无法识别损坏集合
        重评：当需要校验集合完整性时增加健康检查
        """
        from lfx.services.cache.utils import CACHE_DIR

        base_dir = Path(self.persist_directory) if self.persist_directory else Path(CACHE_DIR)
        vector_stores_dir = base_dir / "vector_stores"
        if not vector_stores_dir.exists():
            return []

        return [d.name for d in vector_stores_dir.iterdir() if d.is_dir()]

    def update_build_config(self, build_config: dict, field_value: str, field_name: str | None = None) -> dict:
        """根据模式动态更新组件配置。

        关键路径（三步）：
        1) 先隐藏所有动态字段
        2) 按模式显示/隐藏对应字段
        3) 若选择已有集合则同步集合名

        异常流：无（纯配置变更）。
        决策：Ingest/Retrieve 两套 UI 字段集合
        问题：避免用户在错误模式下看到无效字段
        方案：根据 `mode` 切换 `show` 状态
        代价：字段切换时可能造成用户输入丢失
        重评：当引入模式级表单缓存时减少丢失风险
        """
        if field_name == "mode":
            # 默认隐藏动态字段，按模式再显示
            dynamic_fields = [
                "ingest_data",
                "search_query",
                "search_type",
                "number_of_results",
                "existing_collections",
                "collection_name",
                "embedding",
                "allow_duplicates",
                "limit",
            ]
            for field in dynamic_fields:
                if field in build_config:
                    build_config[field]["show"] = False

            # 按模式显示/隐藏字段
            if field_value == "Ingest":
                if "ingest_data" in build_config:
                    build_config["ingest_data"]["show"] = True
                if "collection_name" in build_config:
                    build_config["collection_name"]["show"] = True
                    build_config["collection_name"]["display_name"] = "Name Your Collection"
                if "persist" in build_config:
                    build_config["persist"]["show"] = True
                if "persist_directory" in build_config:
                    build_config["persist_directory"]["show"] = True
                if "embedding" in build_config:
                    build_config["embedding"]["show"] = True
                if "allow_duplicates" in build_config:
                    build_config["allow_duplicates"]["show"] = True
                if "limit" in build_config:
                    build_config["limit"]["show"] = True
            elif field_value == "Retrieve":
                if "persist" in build_config:
                    build_config["persist"]["show"] = False
                build_config["search_query"]["show"] = True
                build_config["search_type"]["show"] = True
                build_config["number_of_results"]["show"] = True
                build_config["embedding"]["show"] = True
                build_config["collection_name"]["show"] = False
                # 展示已有集合并刷新选项
                if "existing_collections" in build_config:
                    build_config["existing_collections"]["show"] = True
                    build_config["existing_collections"]["options"] = self.list_existing_collections()
        elif field_name == "existing_collections":
            # 选择已有集合时同步 collection_name
            if "collection_name" in build_config:
                build_config["collection_name"]["value"] = field_value

        return build_config

    @override
    @check_cached_vector_store
    def build_vector_store(self) -> Chroma:
        """构建 Chroma 向量库实例。

        关键路径（三步）：
        1) 校验运行环境与依赖
        2) 解析集合名与持久化目录
        3) 创建 Chroma 并写入文档

        异常流：云模式禁用抛异常；缺少依赖抛 `ImportError`。
        排障入口：日志关键字 `Using custom/default persist directory`。
        决策：优先使用已有集合名
        问题：Retrieve 模式需要复用已有集合
        方案：当 `existing_collections` 存在时覆盖 `collection_name`
        代价：用户自定义名称可能被覆盖
        重评：当 UI 改为显式选择时移除此覆盖
        """
        raise_error_if_astra_cloud_disable_component(disable_component_in_astra_cloud_msg)

        try:
            from langchain_chroma import Chroma
        except ImportError as e:
            msg = "Could not import Chroma integration package. Please install it with `pip install langchain-chroma`."
            raise ImportError(msg) from e
        if self.existing_collections:
            self.collection_name = self.existing_collections

        # 使用用户目录或默认缓存目录
        if self.persist_directory:
            base_dir = self.resolve_path(self.persist_directory)
            persist_directory = str(self.get_vector_store_directory(base_dir))
            logger.debug(f"Using custom persist directory: {persist_directory}")
        else:
            persist_directory = self.get_default_persist_dir()
            logger.debug(f"Using default persist directory: {persist_directory}")

        chroma = Chroma(
            persist_directory=persist_directory,
            client=None,
            embedding_function=self.embedding,
            collection_name=self.collection_name,
        )

        self._add_documents_to_vector_store(chroma)
        self.status = chroma_collection_to_data(chroma.get(limit=self.limit))
        return chroma

    def _add_documents_to_vector_store(self, vector_store: "Chroma") -> None:
        """将输入数据写入向量库。

        契约：输入 `vector_store/ingest_data`；输出无；副作用：写入向量库并更新 `self.status`；
        失败语义：输入类型不支持抛 `TypeError`。
        关键路径：1) 预处理入库数据 2) 去重（可选）3) 调用 `add_documents`。
        决策：允许基于内容去重
        问题：避免重复向量导致检索噪声
        方案：当 `allow_duplicates=False` 时对比去掉 `id` 的记录
        代价：去重需要额外读取与比较
        重评：当数据量过大时改为哈希去重
        """
        ingest_data: list | Data | DataFrame = self.ingest_data
        if not ingest_data:
            self.status = ""
            return

        # 使用父类逻辑把 DataFrame 转为 Data
        ingest_data = self._prepare_ingest_data()

        stored_documents_without_id = []
        if self.allow_duplicates:
            stored_data = []
        else:
            stored_data = chroma_collection_to_data(vector_store.get(limit=self.limit))
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
            vector_store.add_documents(documents)
        else:
            self.log("No documents to add to the Vector Store.")

    def perform_search(self) -> DataFrame:
        """执行检索并返回 DataFrame。

        契约：输入 `search_query/search_type/number_of_results` 等；输出 `DataFrame`；
        副作用：无；失败语义：检索异常向上抛出。
        关键路径：1) 调用 `search_documents` 2) 包装为 `DataFrame`。
        决策：使用 `DataFrame` 作为输出形式
        问题：便于下游以表格方式消费结果
        方案：在返回前包装
        代价：增加一层对象包装开销
        重评：当下游接受原始列表时可直接返回
        """
        return DataFrame(self.search_documents())
