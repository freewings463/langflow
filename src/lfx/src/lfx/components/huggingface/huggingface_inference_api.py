"""
模块名称：huggingface_inference_api

本模块提供 Hugging Face 文本向量推理 API 组件封装（TEI）。
主要功能包括：
- 校验推理服务地址并健康检查
- 构建 HuggingFaceInferenceAPIEmbeddings 实例

关键组件：
- `HuggingFaceInferenceAPIEmbeddingsComponent`：向量组件

设计背景：需要对接 Hugging Face Text Embeddings Inference 服务
使用场景：使用本地或远程 TEI 生成向量
注意事项：本地推理可不提供 API Key，但会执行健康检查
"""

from urllib.parse import urlparse

import requests
from langchain_community.embeddings.huggingface import HuggingFaceInferenceAPIEmbeddings

# 注意：后续迁移至 `langchain_huggingface` 实现。
from pydantic import SecretStr
from tenacity import retry, stop_after_attempt, wait_fixed

from lfx.base.embeddings.model import LCEmbeddingsModel
from lfx.field_typing import Embeddings
from lfx.io import MessageTextInput, Output, SecretStrInput


class HuggingFaceInferenceAPIEmbeddingsComponent(LCEmbeddingsModel):
    """Hugging Face Inference API Embeddings 组件。

    契约：远程非本地地址必须提供 `api_key`。
    副作用：对推理端点发起健康检查或初始化请求。
    失败语义：地址非法或健康检查失败抛 `ValueError`。
    决策：本地地址允许无 API Key。
    问题：本地 TEI 部署通常不需要鉴权。
    方案：检测本地地址并使用占位 Key。
    代价：对非本地地址仍需显式 Key。
    重评：当本地部署也要求鉴权时。
    """
    display_name = "Hugging Face Embeddings Inference"
    description = "Generate embeddings using Hugging Face Text Embeddings Inference (TEI)"
    documentation = "https://huggingface.co/docs/text-embeddings-inference/index"
    icon = "HuggingFace"
    name = "HuggingFaceInferenceAPIEmbeddings"

    inputs = [
        SecretStrInput(
            name="api_key",
            display_name="HuggingFace API Key",
            advanced=False,
            info="Required for non-local inference endpoints. Local inference does not require an API Key.",
        ),
        MessageTextInput(
            name="inference_endpoint",
            display_name="Inference Endpoint",
            required=True,
            value="https://api-inference.huggingface.co/models/",
            info="Custom inference endpoint URL.",
        ),
        MessageTextInput(
            name="model_name",
            display_name="Model Name",
            value="BAAI/bge-large-en-v1.5",
            info="The name of the model to use for text embeddings.",
            required=True,
        ),
    ]

    outputs = [
        Output(display_name="Embeddings", name="embeddings", method="build_embeddings"),
    ]

    def validate_inference_endpoint(self, inference_endpoint: str) -> bool:
        """校验推理服务地址格式并执行健康检查。

        契约：URL 必须包含 scheme 与 netloc。
        副作用：向 `/health` 发起 GET 请求。
        失败语义：格式非法或响应异常时抛 `ValueError`。
        """
        parsed_url = urlparse(inference_endpoint)
        if not all([parsed_url.scheme, parsed_url.netloc]):
            msg = (
                f"Invalid inference endpoint format: '{self.inference_endpoint}'. "
                "Please ensure the URL includes both a scheme (e.g., 'http://' or 'https://') and a domain name. "
                "Example: 'http://localhost:8080' or 'https://api.example.com'"
            )
            raise ValueError(msg)

        try:
            response = requests.get(f"{inference_endpoint}/health", timeout=5)
        except requests.RequestException as e:
            msg = (
                f"Inference endpoint '{inference_endpoint}' is not responding. "
                "Please ensure the URL is correct and the service is running."
            )
            raise ValueError(msg) from e

        if response.status_code != requests.codes.ok:
            msg = f"Hugging Face health check failed: {response.status_code}"
            raise ValueError(msg)
        return True

    def get_api_url(self) -> str:
        """获取 API URL。

        契约：当输入为 Hugging Face 域名时直接使用原值。
        失败语义：无显式异常。
        """
        if "huggingface" in self.inference_endpoint.lower():
            return f"{self.inference_endpoint}"
        return self.inference_endpoint

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
    def create_huggingface_embeddings(
        self, api_key: SecretStr, api_url: str, model_name: str
    ) -> HuggingFaceInferenceAPIEmbeddings:
        """构建 Hugging Face Embeddings 实例（带重试）。

        契约：失败时按固定间隔重试 3 次。
        副作用：创建 SDK 客户端。
        失败语义：重试后仍失败则抛异常。
        """
        return HuggingFaceInferenceAPIEmbeddings(api_key=api_key, api_url=api_url, model_name=model_name)

    def build_embeddings(self) -> Embeddings:
        """构建并返回 Embeddings 实例。

        契约：本地地址可无 Key，远程地址必须提供 Key。
        副作用：可能触发健康检查与客户端创建。
        失败语义：连接失败抛 `ValueError`。
        关键路径（三步）：1) 判断地址类型 2) 校验/准备 Key 3) 创建实例。
        决策：本地地址缺失 Key 时自动注入占位 Key。
        问题：本地部署无需鉴权，但 SDK 强制参数存在。
        方案：使用固定占位 Key 通过 SDK 校验。
        代价：可能掩盖误将远程地址当本地的配置问题。
        重评：当 SDK 支持无 Key 初始化时。
        """
        api_url = self.get_api_url()

        is_local_url = (
            api_url.startswith(("http://localhost", "http://127.0.0.1", "http://0.0.0.0", "http://docker"))
            or "huggingface.co" not in api_url.lower()
        )

        if not self.api_key and is_local_url:
            self.validate_inference_endpoint(api_url)
            api_key = SecretStr("APIKeyForLocalDeployment")
        elif not self.api_key:
            msg = "API Key is required for non-local inference endpoints"
            raise ValueError(msg)
        else:
            api_key = SecretStr(self.api_key).get_secret_value()

        try:
            return self.create_huggingface_embeddings(api_key, api_url, self.model_name)
        except Exception as e:
            msg = "Could not connect to Hugging Face Inference API."
            raise ValueError(msg) from e
