"""
模块名称：cohere_embeddings

本模块提供 Cohere 向量模型组件封装。
主要功能包括：
- 构建并返回 Cohere Embeddings 实例
- 支持动态拉取可用模型列表

关键组件：
- `CohereEmbeddingsComponent`：向量组件

设计背景：需要在 Langflow 中使用 Cohere 向量服务
使用场景：文本向量化、检索前向量生成
注意事项：模型列表拉取依赖 API Key
"""

from typing import Any

import cohere
from langchain_cohere import CohereEmbeddings

from lfx.base.models.model import LCModelComponent
from lfx.field_typing import Embeddings
from lfx.io import DropdownInput, FloatInput, IntInput, MessageTextInput, Output, SecretStrInput

HTTP_STATUS_OK = 200


class CohereEmbeddingsComponent(LCModelComponent):
    """Cohere 向量组件。

    契约：需提供 `api_key` 与模型名；返回实现 `Embeddings` 协议的实例。
    副作用：调用 Cohere SDK 并可能触发网络请求。
    失败语义：初始化失败抛 `ValueError`。
    排障入口：异常信息提示 API Key/参数问题。
    """
    display_name = "Cohere Embeddings"
    description = "Generate embeddings using Cohere models."
    icon = "Cohere"
    name = "CohereEmbeddings"

    inputs = [
        SecretStrInput(name="api_key", display_name="Cohere API Key", required=True, real_time_refresh=True),
        DropdownInput(
            name="model_name",
            display_name="Model",
            advanced=False,
            options=[
                "embed-english-v2.0",
                "embed-multilingual-v2.0",
                "embed-english-light-v2.0",
                "embed-multilingual-light-v2.0",
            ],
            value="embed-english-v2.0",
            refresh_button=True,
            combobox=True,
        ),
        MessageTextInput(name="truncate", display_name="Truncate", advanced=True),
        IntInput(name="max_retries", display_name="Max Retries", value=3, advanced=True),
        MessageTextInput(name="user_agent", display_name="User Agent", advanced=True, value="langchain"),
        FloatInput(name="request_timeout", display_name="Request Timeout", advanced=True),
    ]

    outputs = [
        Output(display_name="Embeddings", name="embeddings", method="build_embeddings"),
    ]

    def build_embeddings(self) -> Embeddings:
        """构建 Cohere Embeddings 实例。

        契约：`model_name` 必须在可用列表中。
        副作用：实例化 SDK 客户端。
        失败语义：初始化异常转为 `ValueError`。
        关键路径（三步）：1) 读取输入 2) 组装参数 3) 创建实例。
        决策：`request_timeout` 为空则传 `None`。
        问题：SDK 对空值处理不一致可能导致类型错误。
        方案：显式传入 `None` 表示不设置超时。
        代价：无法区分“用户明确设置 0”与“未设置”。
        重评：当 SDK 明确支持 0 作为无超时时。
        """
        data = None
        try:
            data = CohereEmbeddings(
                cohere_api_key=self.api_key,
                model=self.model_name,
                truncate=self.truncate,
                max_retries=self.max_retries,
                user_agent=self.user_agent,
                request_timeout=self.request_timeout or None,
            )
        except Exception as e:
            msg = (
                "Unable to create Cohere Embeddings. ",
                "Please verify the API key and model parameters, and try again.",
            )
            raise ValueError(msg) from e
        return data

    def get_model(self):
        """拉取 Cohere 可用 Embeddings 模型列表。

        契约：需提供有效 `api_key`。
        副作用：对 Cohere API 发起网络请求。
        失败语义：请求失败抛 `ValueError`。
        """
        try:
            co = cohere.ClientV2(self.api_key)
            response = co.models.list(endpoint="embed")
            models = response.models
            return [model.name for model in models]
        except Exception as e:
            msg = f"Failed to fetch Cohere models. Error: {e}"
            raise ValueError(msg) from e

    async def update_build_config(self, build_config: dict, field_value: Any, field_name: str | None = None):
        """根据输入变化刷新模型列表。

        契约：当 `api_key` 或 `model_name` 变化时更新可用模型。
        副作用：可能触发模型列表网络请求。
        失败语义：由 `get_model` 抛出的异常上抛。
        决策：仅在具备 `api_key` 时拉取模型列表。
        问题：无 Key 拉取会失败并打断配置流程。
        方案：先检查 `api_key` 是否存在。
        代价：需要用户先填写 Key 才能刷新列表。
        重评：当后端提供公开模型列表接口时。
        """
        if field_name in {"model_name", "api_key"}:
            if build_config.get("api_key", {}).get("value", None):
                build_config["model_name"]["options"] = self.get_model()
        else:
            build_config["model_name"]["options"] = field_value
        return build_config
