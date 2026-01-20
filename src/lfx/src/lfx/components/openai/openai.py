"""模块名称：OpenAI Embeddings 组件适配

本模块提供 OpenAI 向量化能力的 Langflow 组件封装。
使用场景：在检索、相似度匹配或向量存储流程中生成文本嵌入。
主要功能包括：
- 组装 OpenAI Embeddings 所需参数
- 支持多种部署/代理/超时配置
- 透传 `model_kwargs` 与默认请求参数

关键组件：
- OpenAIEmbeddingsComponent：嵌入模型组件入口

设计背景：统一 Langflow Embeddings 接口，兼容 OpenAI 与代理部署
注意事项：`openai_api_key` 为必填；`tiktoken_enable=False` 需要本地 `transformers`
"""

from langchain_openai import OpenAIEmbeddings

from lfx.base.embeddings.model import LCEmbeddingsModel
from lfx.base.models.openai_constants import OPENAI_EMBEDDING_MODEL_NAMES
from lfx.field_typing import Embeddings
from lfx.io import BoolInput, DictInput, DropdownInput, FloatInput, IntInput, MessageTextInput, SecretStrInput


class OpenAIEmbeddingsComponent(LCEmbeddingsModel):
    """OpenAI Embeddings 组件，输出向量化能力。

    契约：输入模型、密钥与请求参数，输出 `Embeddings`
    关键路径：1) 读取组件字段 2) 组装参数 3) 初始化 `OpenAIEmbeddings`
    副作用：可能触发网络初始化；依赖本地 `tiktoken` 或 `transformers`
    异常流：底层 SDK 异常直接上抛
    排障入口：OpenAI SDK 抛错消息或网络错误
    """
    display_name = "OpenAI Embeddings"
    description = "Generate embeddings using OpenAI models."
    icon = "OpenAI"
    name = "OpenAIEmbeddings"

    inputs = [
        DictInput(
            name="default_headers",
            display_name="Default Headers",
            advanced=True,
            info="Default headers to use for the API request.",
        ),
        DictInput(
            name="default_query",
            display_name="Default Query",
            advanced=True,
            info="Default query parameters to use for the API request.",
        ),
        IntInput(name="chunk_size", display_name="Chunk Size", advanced=True, value=1000),
        MessageTextInput(name="client", display_name="Client", advanced=True),
        MessageTextInput(name="deployment", display_name="Deployment", advanced=True),
        IntInput(name="embedding_ctx_length", display_name="Embedding Context Length", advanced=True, value=1536),
        IntInput(name="max_retries", display_name="Max Retries", value=3, advanced=True),
        DropdownInput(
            name="model",
            display_name="Model",
            advanced=False,
            options=OPENAI_EMBEDDING_MODEL_NAMES,
            value="text-embedding-3-small",
        ),
        DictInput(name="model_kwargs", display_name="Model Kwargs", advanced=True),
        SecretStrInput(name="openai_api_key", display_name="OpenAI API Key", value="OPENAI_API_KEY", required=True),
        MessageTextInput(name="openai_api_base", display_name="OpenAI API Base", advanced=True),
        MessageTextInput(name="openai_api_type", display_name="OpenAI API Type", advanced=True),
        MessageTextInput(name="openai_api_version", display_name="OpenAI API Version", advanced=True),
        MessageTextInput(
            name="openai_organization",
            display_name="OpenAI Organization",
            advanced=True,
        ),
        MessageTextInput(name="openai_proxy", display_name="OpenAI Proxy", advanced=True),
        FloatInput(name="request_timeout", display_name="Request Timeout", advanced=True),
        BoolInput(name="show_progress_bar", display_name="Show Progress Bar", advanced=True),
        BoolInput(name="skip_empty", display_name="Skip Empty", advanced=True),
        MessageTextInput(
            name="tiktoken_model_name",
            display_name="TikToken Model Name",
            advanced=True,
        ),
        BoolInput(
            name="tiktoken_enable",
            display_name="TikToken Enable",
            advanced=True,
            value=True,
            info="If False, you must have transformers installed.",
        ),
        IntInput(
            name="dimensions",
            display_name="Dimensions",
            info="The number of dimensions the resulting output embeddings should have. "
            "Only supported by certain models.",
            advanced=True,
        ),
    ]

    def build_embeddings(self) -> Embeddings:
        """构建 OpenAI Embeddings 实例。

        契约：返回 `Embeddings`；`None` 代表使用 SDK 默认值
        副作用：可能进行网络初始化；读取代理与组织配置
        异常流：底层 SDK 异常直接上抛
        """
        return OpenAIEmbeddings(
            client=self.client or None,
            model=self.model,
            dimensions=self.dimensions or None,
            deployment=self.deployment or None,
            api_version=self.openai_api_version or None,
            base_url=self.openai_api_base or None,
            openai_api_type=self.openai_api_type or None,
            openai_proxy=self.openai_proxy or None,
            embedding_ctx_length=self.embedding_ctx_length,
            api_key=self.openai_api_key or None,
            organization=self.openai_organization or None,
            allowed_special="all",
            disallowed_special="all",
            chunk_size=self.chunk_size,
            max_retries=self.max_retries,
            timeout=self.request_timeout or None,
            tiktoken_enabled=self.tiktoken_enable,
            tiktoken_model_name=self.tiktoken_model_name or None,
            show_progress_bar=self.show_progress_bar,
            model_kwargs=self.model_kwargs,
            skip_empty=self.skip_empty,
            default_headers=self.default_headers or None,
            default_query=self.default_query or None,
        )
