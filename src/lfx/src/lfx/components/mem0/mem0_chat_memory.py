"""模块名称：Mem0 记忆组件适配层

本模块提供基于 `mem0` 的聊天记忆组件，用于 Langflow 组件系统的消息写入与检索。
使用场景：需要在对话流中持久化用户消息并进行语义检索。
主要功能包括：
- 根据 `mem0_config` 与 API Key 构建本地或云端 Mem0 实例
- 写入单条消息并附加 `metadata`
- 按 `search_query` 或 `user_id` 拉取相关记忆

关键组件：
- Mem0MemoryComponent：组件入口，封装构建、写入、查询流程

设计背景：在 Langflow 组件体系中统一记忆存储接口，同时保留本地/云端切换能力
注意事项：Astra Cloud 环境禁用；缺少 `user_id`/`ingest_message` 时不会写入
"""

import os

from mem0 import Memory, MemoryClient

from lfx.base.memory.model import LCChatMemoryComponent
from lfx.inputs.inputs import DictInput, HandleInput, MessageTextInput, NestedDictInput, SecretStrInput
from lfx.io import Output
from lfx.log.logger import logger
from lfx.schema.data import Data
from lfx.utils.validate_cloud import raise_error_if_astra_cloud_disable_component

disable_component_in_astra_cloud_msg = (
    "Mem0 chat memory is not supported in Astra cloud environment. Please use local storage mode or mem0 cloud."
)


class Mem0MemoryComponent(LCChatMemoryComponent):
    """Mem0 聊天记忆组件，提供写入与检索。

    契约：输入 `mem0_config`/`mem0_api_key`/`openai_api_key`，输出 `Memory` 或检索结果 `Data`
    关键路径：1) 构建实例 2) 写入消息 3) 按查询或用户检索
    副作用：写入 Mem0 存储并记录日志，可能修改进程环境变量
    异常流：Astra Cloud 禁用抛错；Mem0 未安装抛 `ImportError`
    排障入口：日志关键字 `Missing 'ingest_message'` / `Failed to add message` / `Failed to retrieve related memories`
    决策：以 `Memory`/`MemoryClient` 双路径支持本地与云端
    问题：需要兼容本地存储与 Mem0 Cloud
    方案：无 `mem0_api_key` 走 `Memory`，有 key 走 `MemoryClient`
    代价：两套初始化路径增加配置分支
    重评：当 Mem0 SDK 统一初始化入口或本地/云端差异消失时
    """
    display_name = "Mem0 Chat Memory"
    description = "Retrieves and stores chat messages using Mem0 memory storage."
    name = "mem0_chat_memory"
    icon: str = "Mem0"
    inputs = [
        NestedDictInput(
            name="mem0_config",
            display_name="Mem0 Configuration",
            info="""Configuration dictionary for initializing Mem0 memory instance.
                    Example:
                    {
                        "graph_store": {
                            "provider": "neo4j",
                            "config": {
                                "url": "neo4j+s://your-neo4j-url",
                                "username": "neo4j",
                                "password": "your-password"
                            }
                        },
                        "version": "v1.1"
                    }""",
            input_types=["Data"],
        ),
        MessageTextInput(
            name="ingest_message",
            display_name="Message to Ingest",
            info="The message content to be ingested into Mem0 memory.",
        ),
        HandleInput(
            name="existing_memory",
            display_name="Existing Memory Instance",
            input_types=["Memory"],
            info="Optional existing Mem0 memory instance. If not provided, a new instance will be created.",
        ),
        MessageTextInput(
            name="user_id", display_name="User ID", info="Identifier for the user associated with the messages."
        ),
        MessageTextInput(
            name="search_query", display_name="Search Query", info="Input text for searching related memories in Mem0."
        ),
        SecretStrInput(
            name="mem0_api_key",
            display_name="Mem0 API Key",
            info="API key for Mem0 platform. Leave empty to use the local version.",
        ),
        DictInput(
            name="metadata",
            display_name="Metadata",
            info="Additional metadata to associate with the ingested message.",
            advanced=True,
        ),
        SecretStrInput(
            name="openai_api_key",
            display_name="OpenAI API Key",
            required=False,
            info="API key for OpenAI. Required if using OpenAI Embeddings without a provided configuration.",
        ),
    ]

    outputs = [
        Output(name="memory", display_name="Mem0 Memory", method="ingest_data"),
        Output(
            name="search_results",
            display_name="Search Results",
            method="build_search_results",
        ),
    ]

    def build_mem0(self) -> Memory:
        """构建 Mem0 实例，兼容本地与云端。

        契约：读取组件字段构建 `Memory`/`MemoryClient`；若提供 `openai_api_key` 写入环境变量
        关键路径：1) Astra Cloud 禁用检查 2) 注入 `OPENAI_API_KEY` 3) 分支初始化
        副作用：可能设置进程级 `OPENAI_API_KEY`
        异常流：`mem0` 未安装抛 `ImportError`；其他异常直接上抛
        排障入口：`ImportError` 提示安装 `mem0ai`
        决策：通过环境变量注入 `OPENAI_API_KEY`
        问题：Mem0 SDK 依赖环境变量读取 OpenAI Key
        方案：若传入 `openai_api_key`，写入 `OPENAI_API_KEY`
        代价：进程级副作用，可能影响同进程其他调用
        重评：当 Mem0 支持显式传参或本组件改为隔离进程时
        """
        # 注意：构建前阻断 Astra Cloud，避免初始化受限依赖。
        raise_error_if_astra_cloud_disable_component(disable_component_in_astra_cloud_msg)
        if self.openai_api_key:
            # 注意：`mem0` 读取环境变量，避免在初始化路径中丢失 OpenAI Key。
            os.environ["OPENAI_API_KEY"] = self.openai_api_key

        try:
            if not self.mem0_api_key:
                return Memory.from_config(config_dict=dict(self.mem0_config)) if self.mem0_config else Memory()
            if self.mem0_config:
                return MemoryClient.from_config(api_key=self.mem0_api_key, config_dict=dict(self.mem0_config))
            return MemoryClient(api_key=self.mem0_api_key)
        except ImportError as e:
            msg = "Mem0 is not properly installed. Please install it with 'pip install -U mem0ai'."
            raise ImportError(msg) from e

    def ingest_data(self) -> Memory:
        """写入单条消息并返回 Mem0 实例。

        契约：需要 `ingest_message` 与 `user_id`；返回可继续使用的 `Memory`
        关键路径：1) Astra Cloud 禁用检查 2) 选择已有或新建实例 3) 写入消息
        副作用：向 Mem0 存储写入消息并记录日志
        异常流：缺少 `ingest_message`/`user_id` 时仅告警并返回；写入失败抛异常
        排障入口：日志关键字 `Missing 'ingest_message'` / `Failed to add message`
        决策：缺失必填字段时不抛错而返回现有实例
        问题：组件在编排中可能先被预构建，字段后续才补齐
        方案：记录告警并跳过写入，保持管线可继续
        代价：可能产生“未写入但无异常”的结果
        重评：当上游保证字段完备或需要强一致写入时
        """
        # 注意：写入前阻断 Astra Cloud，避免产生不可回滚写入。
        raise_error_if_astra_cloud_disable_component(disable_component_in_astra_cloud_msg)
        mem0_memory = self.existing_memory or self.build_mem0()

        if not self.ingest_message or not self.user_id:
            logger.warning("Missing 'ingest_message' or 'user_id'; cannot ingest data.")
            return mem0_memory

        metadata = self.metadata or {}

        logger.info("Ingesting message for user_id: %s", self.user_id)

        try:
            mem0_memory.add(self.ingest_message, user_id=self.user_id, metadata=metadata)
        except Exception:
            logger.exception("Failed to add message to Mem0 memory.")
            raise

        return mem0_memory

    def build_search_results(self) -> Data:
        """检索相关记忆并返回结果。

        契约：使用 `search_query` 与 `user_id` 检索，返回 `Data` 结构
        关键路径：1) 先写入最新消息 2) 有查询则搜索 3) 无查询则按用户拉取
        副作用：会触发一次写入流程并记录日志
        异常流：检索失败抛异常并记录日志
        排障入口：日志关键字 `Failed to retrieve related memories`
        决策：检索前调用 `ingest_data` 以保证最新消息可被搜索
        问题：搜索时需要包含本次输入的最新消息
        方案：统一先写入后检索
        代价：检索也会触发写入，增加一次外部调用
        重评：当系统改为显式控制“写入/检索”阶段时
        """
        # 注意：检索前阻断 Astra Cloud，避免触发禁用路径。
        raise_error_if_astra_cloud_disable_component(disable_component_in_astra_cloud_msg)
        mem0_memory = self.ingest_data()
        search_query = self.search_query
        user_id = self.user_id

        logger.info("Search query: %s", search_query)

        try:
            if search_query:
                logger.info("Performing search with query.")
                related_memories = mem0_memory.search(query=search_query, user_id=user_id)
            else:
                logger.info("Retrieving all memories for user_id: %s", user_id)
                related_memories = mem0_memory.get_all(user_id=user_id)
        except Exception:
            logger.exception("Failed to retrieve related memories from Mem0.")
            raise

        logger.info("Related memories retrieved: %s", related_memories)
        return related_memories
