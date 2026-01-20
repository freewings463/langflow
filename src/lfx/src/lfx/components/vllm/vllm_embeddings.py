"""模块名称：vLLM 向量嵌入组件

本模块封装基于 OpenAI 兼容接口的 vLLM 嵌入模型。
主要功能包括：配置模型名与 API 端点、设置维度与请求参数、构建 `OpenAIEmbeddings`。

关键组件：
- `VllmEmbeddingsComponent`：vLLM 向量嵌入组件入口

设计背景：统一 vLLM 推理服务的向量嵌入接入方式。
注意事项：`dimensions` 仅部分模型支持；`api_base` 默认本地 vLLM 端点。
"""

from langchain_openai import OpenAIEmbeddings

from lfx.base.embeddings.model import LCEmbeddingsModel
from lfx.field_typing import Embeddings
from lfx.io import BoolInput, DictInput, FloatInput, IntInput, MessageTextInput, SecretStrInput


class VllmEmbeddingsComponent(LCEmbeddingsModel):
    """vLLM 向量嵌入组件。

    契约：输入 `model_name/api_base/api_key/dimensions` 等；输出 `Embeddings`；
    副作用：无；失败语义：底层请求异常由客户端抛出。
    关键路径：1) 组装参数 2) 创建 `OpenAIEmbeddings` 实例 3) 返回嵌入模型。
    决策：将空值参数传为 `None`
    问题：避免把空字符串作为有效参数传入
    方案：对 `api_key/timeout/default_*` 做 None 化
    代价：服务端默认值不可控
    重评：当需要固定默认值时改为显式传参
    """
    display_name = "vLLM Embeddings"
    description = "Generate embeddings using vLLM models via OpenAI-compatible API."
    icon = "vLLM"
    name = "vLLMEmbeddings"

    inputs = [
        MessageTextInput(
            name="model_name",
            display_name="Model Name",
            advanced=False,
            info="The name of the vLLM embeddings model to use (e.g., 'BAAI/bge-large-en-v1.5').",
            value="BAAI/bge-large-en-v1.5",
        ),
        MessageTextInput(
            name="api_base",
            display_name="vLLM API Base",
            advanced=False,
            info="The base URL of the vLLM API server. Defaults to http://localhost:8000/v1 for local vLLM server.",
            value="http://localhost:8000/v1",
        ),
        SecretStrInput(
            name="api_key",
            display_name="API Key",
            info="The API Key to use for the vLLM model (optional for local servers).",
            advanced=False,
            value="",
            required=False,
        ),
        IntInput(
            name="dimensions",
            display_name="Dimensions",
            info="The number of dimensions the resulting output embeddings should have. "
            "Only supported by certain models.",
            advanced=True,
        ),
        IntInput(
            name="chunk_size",
            display_name="Chunk Size",
            advanced=True,
            value=1000,
            info="The chunk size to use when processing documents.",
        ),
        IntInput(
            name="max_retries",
            display_name="Max Retries",
            value=3,
            advanced=True,
            info="Maximum number of retries for failed requests.",
        ),
        FloatInput(
            name="request_timeout",
            display_name="Request Timeout",
            advanced=True,
            info="Timeout for requests to vLLM API in seconds.",
        ),
        BoolInput(
            name="show_progress_bar",
            display_name="Show Progress Bar",
            advanced=True,
            info="Whether to show a progress bar when processing multiple documents.",
        ),
        BoolInput(
            name="skip_empty",
            display_name="Skip Empty",
            advanced=True,
            info="Whether to skip empty documents.",
        ),
        DictInput(
            name="model_kwargs",
            display_name="Model Kwargs",
            advanced=True,
            info="Additional keyword arguments to pass to the model.",
        ),
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
    ]

    def build_embeddings(self) -> Embeddings:
        """构建 vLLM 嵌入模型实例。

        契约：输入 `model_name/api_base` 等；输出 `OpenAIEmbeddings`；
        副作用无；失败语义：请求异常透传。
        关键路径：1) 规范化参数 2) 初始化 embeddings 实例。
        决策：`dimensions` 为空时不传
        问题：部分模型不支持自定义维度
        方案：空值回退为 `None`
        代价：无法强制统一维度
        重评：当模型统一支持维度时改为必填
        """
        return OpenAIEmbeddings(
            model=self.model_name,
            base_url=self.api_base or "http://localhost:8000/v1",
            api_key=self.api_key or None,
            dimensions=self.dimensions or None,
            chunk_size=self.chunk_size,
            max_retries=self.max_retries,
            timeout=self.request_timeout or None,
            show_progress_bar=self.show_progress_bar,
            skip_empty=self.skip_empty,
            model_kwargs=self.model_kwargs,
            default_headers=self.default_headers or None,
            default_query=self.default_query or None,
        )
