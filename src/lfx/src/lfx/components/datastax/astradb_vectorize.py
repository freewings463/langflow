"""
模块名称：Astra Vectorize 配置组件

本模块提供 Astra DB Vectorize 的配置生成，用于服务端嵌入向量生成。主要功能包括：
- 选择向量化提供商与模型
- 组装 Vectorize 配置参数并输出

关键组件：
- `AstraVectorizeComponent`

设计背景：将向量化配置作为组件输出，供向量库组件复用。
使用场景：Astra DB Collection 使用服务端向量化。
注意事项：输出结构需与 `astrapy` 的配置约定一致。
"""

from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import DictInput, DropdownInput, MessageTextInput, SecretStrInput
from lfx.template.field.base import Output


class AstraVectorizeComponent(Component):
    """Astra Vectorize 配置组件

    契约：输入 provider/model/认证等参数；输出配置字典；
    副作用：无；失败语义：provider 缺失会导致 KeyError。
    关键路径：1) 映射 provider 名称 2) 组装认证参数 3) 返回配置结构。
    决策：将 `api_key_name` 写入 `authentication.providerKey`。
    问题：Astra 侧需要 providerKey 指向已存密钥。
    方案：提供显式覆盖入口。
    代价：配置错误会导致向量化失败。
    重评：当统一改为 provider_api_key 直传时。
    """
    display_name: str = "Astra Vectorize"
    description: str = "Configuration options for Astra Vectorize server-side embeddings. "
    documentation: str = "https://docs.datastax.com/en/astra-db-serverless/databases/embedding-generation.html"
    legacy = True
    icon = "AstraDB"
    name = "AstraVectorize"
    replacement = ["datastax.AstraDB"]

    VECTORIZE_PROVIDERS_MAPPING = {
        "Azure OpenAI": ["azureOpenAI", ["text-embedding-3-small", "text-embedding-3-large", "text-embedding-ada-002"]],
        "Hugging Face - Dedicated": ["huggingfaceDedicated", ["endpoint-defined-model"]],
        "Hugging Face - Serverless": [
            "huggingface",
            [
                "sentence-transformers/all-MiniLM-L6-v2",
                "intfloat/multilingual-e5-large",
                "intfloat/multilingual-e5-large-instruct",
                "BAAI/bge-small-en-v1.5",
                "BAAI/bge-base-en-v1.5",
                "BAAI/bge-large-en-v1.5",
            ],
        ],
        "Jina AI": [
            "jinaAI",
            [
                "jina-embeddings-v2-base-en",
                "jina-embeddings-v2-base-de",
                "jina-embeddings-v2-base-es",
                "jina-embeddings-v2-base-code",
                "jina-embeddings-v2-base-zh",
            ],
        ],
        "Mistral AI": ["mistral", ["mistral-embed"]],
        "NVIDIA": ["nvidia", ["NV-Embed-QA"]],
        "OpenAI": ["openai", ["text-embedding-3-small", "text-embedding-3-large", "text-embedding-ada-002"]],
        "Upstage": ["upstageAI", ["solar-embedding-1-large"]],
        "Voyage AI": [
            "voyageAI",
            ["voyage-large-2-instruct", "voyage-law-2", "voyage-code-2", "voyage-large-2", "voyage-2"],
        ],
    }
    VECTORIZE_MODELS_STR = "\n\n".join(
        [provider + ": " + (", ".join(models[1])) for provider, models in VECTORIZE_PROVIDERS_MAPPING.items()]
    )

    inputs = [
        DropdownInput(
            name="provider",
            display_name="Provider",
            options=VECTORIZE_PROVIDERS_MAPPING.keys(),
            value="",
            required=True,
        ),
        MessageTextInput(
            name="model_name",
            display_name="Model Name",
            info="The embedding model to use for the selected provider. Each provider has a different set of models "
            f"available (full list at https://docs.datastax.com/en/astra-db-serverless/databases/embedding-generation.html):\n\n{VECTORIZE_MODELS_STR}",
            required=True,
        ),
        MessageTextInput(
            name="api_key_name",
            display_name="API Key name",
            info="The name of the embeddings provider API key stored on Astra. "
            "If set, it will override the 'ProviderKey' in the authentication parameters.",
        ),
        DictInput(
            name="authentication",
            display_name="Authentication parameters",
            is_list=True,
            advanced=True,
        ),
        SecretStrInput(
            name="provider_api_key",
            display_name="Provider API Key",
            info="An alternative to the Astra Authentication that passes an API key for the provider with each request "
            "to Astra DB. "
            "This may be used when Vectorize is configured for the collection, "
            "but no corresponding provider secret is stored within Astra's key management system.",
            advanced=True,
        ),
        DictInput(
            name="authentication",
            display_name="Authentication Parameters",
            is_list=True,
            advanced=True,
        ),
        DictInput(
            name="model_parameters",
            display_name="Model Parameters",
            advanced=True,
            is_list=True,
        ),
    ]
    outputs = [
        Output(display_name="Vectorize", name="config", method="build_options", types=["dict"]),
    ]

    def build_options(self) -> dict[str, Any]:
        """生成 Vectorize 配置字典

        契约：返回包含 `collection_vector_service_options` 的字典；
        副作用：无；失败语义：provider 未配置时抛异常。
        关键路径：读取 provider 映射 -> 合并认证参数 -> 返回配置。
        决策：将模型参数留给 `model_parameters` 透传。
        问题：不同 provider 的参数差异大。
        方案：以字典透传避免强校验。
        代价：运行期可能出现参数不兼容。
        重评：当有统一的参数校验规范时。
        """
        provider_value = self.VECTORIZE_PROVIDERS_MAPPING[self.provider][0]
        authentication = {**(self.authentication or {})}
        api_key_name = self.api_key_name
        if api_key_name:
            authentication["providerKey"] = api_key_name
        return {
            # 注意：结构需与 `astrapy.info.VectorServiceOptions` 对齐
            "collection_vector_service_options": {
                "provider": provider_value,
                "modelName": self.model_name,
                "authentication": authentication,
                "parameters": self.model_parameters or {},
            },
            "collection_embedding_api_key": self.provider_api_key,
        }
