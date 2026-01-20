"""
模块名称：Mistral 向量嵌入组件

模块目的：提供可在 Langflow 运行时调用的 Mistral 向量嵌入组件。
使用场景：在流程中对文本进行向量化以支持检索或相似度计算。
主要功能包括：
- 定义 Embeddings 输入参数（模型、超时、并发、endpoint）
- 使用 `MistralAIEmbeddings` 构建客户端

关键组件：
- `MistralAIEmbeddingsComponent`：嵌入组件入口

设计背景：复用 LangChain Embeddings 接口以保持组件一致性。
注意：缺失 `mistral_api_key` 会抛 `ValueError`，调用方需提示配置问题。
"""

from langchain_mistralai import MistralAIEmbeddings
from pydantic.v1 import SecretStr

from lfx.base.models.model import LCModelComponent
from lfx.field_typing import Embeddings
from lfx.io import DropdownInput, IntInput, MessageTextInput, Output, SecretStrInput


class MistralAIEmbeddingsComponent(LCModelComponent):
    """Mistral 向量嵌入组件。

    契约：输入 `mistral_api_key`/`model` 等参数，输出 `Embeddings`。
    关键路径：由 `build_embeddings` 完成校验与客户端构建。

    决策：通过 `MistralAIEmbeddings` 适配 LangChain Embeddings 接口
    问题：需要在 Langflow 中统一 Embeddings 组件契约
    方案：复用 `langchain_mistralai` 的封装能力
    代价：受其功能与版本支持范围限制
    重评：当需原生 SDK 或上游不兼容时
    """
    display_name = "MistralAI Embeddings"
    description = "Generate embeddings using MistralAI models."
    icon = "MistralAI"
    name = "MistalAIEmbeddings"

    inputs = [
        DropdownInput(
            name="model",
            display_name="Model",
            advanced=False,
            options=["mistral-embed"],
            value="mistral-embed",
        ),
        SecretStrInput(name="mistral_api_key", display_name="Mistral API Key", required=True),
        IntInput(
            name="max_concurrent_requests",
            display_name="Max Concurrent Requests",
            advanced=True,
            value=64,
        ),
        IntInput(name="max_retries", display_name="Max Retries", advanced=True, value=5),
        IntInput(name="timeout", display_name="Request Timeout", advanced=True, value=120),
        MessageTextInput(
            name="endpoint",
            display_name="API Endpoint",
            advanced=True,
            value="https://api.mistral.ai/v1/",
        ),
    ]

    outputs = [
        Output(display_name="Embeddings", name="embeddings", method="build_embeddings"),
    ]

    def build_embeddings(self) -> Embeddings:
        """构建 Mistral Embeddings 客户端供运行时调用。

        契约：读取组件字段并返回 `Embeddings`；`mistral_api_key` 必填。
        副作用：创建外部 API 客户端（网络请求在后续向量化调用时发生）。

        关键路径（三步）：
        1) 校验 `mistral_api_key`
        2) 解密密钥并初始化 `MistralAIEmbeddings`
        3) 返回 Embeddings 实例

        注意：异常流为缺失密钥抛 `ValueError`；初始化异常向外传播。
        性能：远端向量化耗时，吞吐受 `max_concurrent_requests` 影响。
        排障：关注异常堆栈及 API 返回的错误信息。
        """
        if not self.mistral_api_key:
            msg = "Mistral API Key is required"
            raise ValueError(msg)

        api_key = SecretStr(self.mistral_api_key).get_secret_value()

        return MistralAIEmbeddings(
            api_key=api_key,
            model=self.model,
            endpoint=self.endpoint,
            max_concurrent_requests=self.max_concurrent_requests,
            max_retries=self.max_retries,
            timeout=self.timeout,
        )
