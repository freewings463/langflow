"""
模块名称：`Azure OpenAI` 向量模型组件

本模块提供基于 `Azure OpenAI` 的向量模型组件，用于构建 `AzureOpenAIEmbeddings` 实例。
主要功能包括：
- 定义 `Azure OpenAI` Embeddings 参数配置
- 初始化并返回向量模型实例

关键组件：
- `AzureOpenAIEmbeddingsComponent`

设计背景：统一 `Azure OpenAI` Embeddings 接入入口。
注意事项：连接失败会抛 `ValueError`，需检查 `endpoint`/`deployment`/`api_version`。
"""

from langchain_openai import AzureOpenAIEmbeddings

from lfx.base.models.model import LCModelComponent
from lfx.base.models.openai_constants import OPENAI_EMBEDDING_MODEL_NAMES
from lfx.field_typing import Embeddings
from lfx.io import DropdownInput, IntInput, MessageTextInput, Output, SecretStrInput


class AzureOpenAIEmbeddingsComponent(LCModelComponent):
    """`Azure OpenAI` 向量模型组件

    契约：
    - 输入：模型名、`endpoint`、`deployment`、`api_version`、`api_key` 等配置
    - 输出：`Embeddings` 实例
    - 副作用：无
    - 失败语义：连接失败时抛 `ValueError`
    """
    display_name: str = "Azure OpenAI Embeddings"
    description: str = "Generate embeddings using Azure OpenAI models."
    documentation: str = "https://python.langchain.com/docs/integrations/text_embedding/azureopenai"
    icon = "Azure"
    name = "AzureOpenAIEmbeddings"

    API_VERSION_OPTIONS = [
        "2022-12-01",
        "2023-03-15-preview",
        "2023-05-15",
        "2023-06-01-preview",
        "2023-07-01-preview",
        "2023-08-01-preview",
    ]

    inputs = [
        DropdownInput(
            name="model",
            display_name="Model",
            advanced=False,
            options=OPENAI_EMBEDDING_MODEL_NAMES,
            value=OPENAI_EMBEDDING_MODEL_NAMES[0],
        ),
        MessageTextInput(
            name="azure_endpoint",
            display_name="Azure Endpoint",
            required=True,
            info="Your Azure endpoint, including the resource. Example: `https://example-resource.azure.openai.com/`",
        ),
        MessageTextInput(
            name="azure_deployment",
            display_name="Deployment Name",
            required=True,
        ),
        DropdownInput(
            name="api_version",
            display_name="API Version",
            options=API_VERSION_OPTIONS,
            value=API_VERSION_OPTIONS[-1],
            advanced=True,
        ),
        SecretStrInput(
            name="api_key",
            display_name="Azure OpenAI API Key",
            required=True,
        ),
        IntInput(
            name="dimensions",
            display_name="Dimensions",
            info="The number of dimensions the resulting output embeddings should have. "
            "Only supported by certain models.",
            advanced=True,
        ),
    ]

    outputs = [
        Output(display_name="Embeddings", name="embeddings", method="build_embeddings"),
    ]

    def build_embeddings(self) -> Embeddings:
        """构建 `AzureOpenAIEmbeddings` 实例

        契约：
        - 输入：无（使用组件字段）
        - 输出：`Embeddings` 实例
        - 副作用：无
        - 失败语义：构建失败时抛异常
        """
        try:
            embeddings = AzureOpenAIEmbeddings(
                model=self.model,
                azure_endpoint=self.azure_endpoint,
                azure_deployment=self.azure_deployment,
                api_version=self.api_version,
                api_key=self.api_key,
                dimensions=self.dimensions or None,
            )
        except Exception as e:
            msg = f"Could not connect to AzureOpenAIEmbeddings API: {e}"
            raise ValueError(msg) from e

        return embeddings
