"""
模块名称：TwelveLabs 文本向量组件

本模块封装 TwelveLabs 文本向量接口，提供文档与查询向量生成能力。
主要功能包括：
- 构建 TwelveLabs 客户端并调用文本向量接口
- 将结果转换为浮点数组并返回

关键组件：
- `TwelveLabsTextEmbeddings`
- `TwelveLabsTextEmbeddingsComponent`

设计背景：在 Langflow 中统一输出 `Embeddings` 接口实现。
注意事项：当前仅取首个片段向量。
"""

from twelvelabs import TwelveLabs

from lfx.base.embeddings.model import LCEmbeddingsModel
from lfx.field_typing import Embeddings
from lfx.io import DropdownInput, FloatInput, IntInput, SecretStrInput


class TwelveLabsTextEmbeddings(Embeddings):
    """TwelveLabs 文本向量实现。

    契约：
    - 输入：文本列表或单条文本
    - 输出：向量列表或单向量
    - 副作用：发起 TwelveLabs API 请求
    - 失败语义：API 异常向上传递
    """

    def __init__(self, api_key: str, model: str) -> None:
        self.client = TwelveLabs(api_key=api_key)
        self.model = model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """为文本列表生成向量，忽略空字符串。

        契约：
        - 输入：文本列表
        - 输出：二维向量列表
        - 副作用：调用 TwelveLabs API
        - 失败语义：API 异常向上传递
        """
        all_embeddings: list[list[float]] = []
        for text in texts:
            if not text:
                continue

            result = self.client.embed.create(model_name=self.model, text=text)

            if result.text_embedding and result.text_embedding.segments:
                for segment in result.text_embedding.segments:
                    all_embeddings.append([float(x) for x in segment.embeddings_float])
                    break  # 注意：暂只取首个片段

        return all_embeddings

    def embed_query(self, text: str) -> list[float]:
        """为单条文本生成向量，未命中返回空列表。

        契约：
        - 输入：单条文本
        - 输出：向量列表
        - 副作用：调用 TwelveLabs API
        - 失败语义：API 异常向上传递
        """
        result = self.client.embed.create(model_name=self.model, text=text)

        if result.text_embedding and result.text_embedding.segments:
            return [float(x) for x in result.text_embedding.segments[0].embeddings_float]
        return []


class TwelveLabsTextEmbeddingsComponent(LCEmbeddingsModel):
    """TwelveLabs 文本向量组件。

    契约：
    - 输入：API Key 与模型名
    - 输出：`Embeddings` 实例
    - 副作用：构造客户端对象
    - 失败语义：构造异常向上传递
    """

    display_name = "TwelveLabs Text Embeddings"
    description = "Generate embeddings using TwelveLabs text embedding models."
    icon = "TwelveLabs"
    name = "TwelveLabsTextEmbeddings"
    documentation = "https://github.com/twelvelabs-io/twelvelabs-developer-experience/blob/main/integrations/Langflow/TWELVE_LABS_COMPONENTS_README.md"

    inputs = [
        SecretStrInput(name="api_key", display_name="TwelveLabs API Key", value="TWELVELABS_API_KEY", required=True),
        DropdownInput(
            name="model",
            display_name="Model",
            advanced=False,
            options=["Marengo-retrieval-2.7"],
            value="Marengo-retrieval-2.7",
        ),
        IntInput(name="max_retries", display_name="Max Retries", value=3, advanced=True),
        FloatInput(name="request_timeout", display_name="Request Timeout", advanced=True),
    ]

    def build_embeddings(self) -> Embeddings:
        """构建 TwelveLabs 文本向量客户端。

        契约：
        - 输入：API Key 与模型名
        - 输出：`Embeddings` 实例
        - 副作用：构造 TwelveLabs 客户端
        - 失败语义：构造异常向上传递
        """
        return TwelveLabsTextEmbeddings(api_key=self.api_key, model=self.model)
