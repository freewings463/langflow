"""
模块名称：text_embedder

本模块提供文本 embedding 生成组件，用于将 Message 转为向量表示。
主要功能包括：
- 功能1：调用 embedding 模型生成向量。
- 功能2：将文本与向量封装为 `Data` 输出。

使用场景：在流程中为文本生成 embedding 以供检索、聚类或相似度计算。
关键组件：
- 类 `TextEmbedderComponent`

设计背景：统一 embedding 生成流程，减少组件间重复逻辑。
注意事项：输入模型需实现 `embed_documents`；空文本会返回错误。
"""

from typing import TYPE_CHECKING

from lfx.custom.custom_component.component import Component
from lfx.io import HandleInput, MessageInput, Output
from lfx.log.logger import logger
from lfx.schema.data import Data

if TYPE_CHECKING:
    from lfx.field_typing import Embeddings
    from lfx.schema.message import Message


class TextEmbedderComponent(Component):
    """文本 embedding 生成组件。

    契约：输入 `embedding_model` 与 `message`；输出 `Data(text, embeddings)`。
    关键路径：
    1) 校验模型与文本有效性；
    2) 调用 `embed_documents` 生成向量；
    3) 返回封装后的 `Data`。
    异常流：模型不兼容/文本为空/生成失败时返回错误 `Data`。
    排障入口：日志记录异常并更新 `self.status`。
    决策：
    问题：embedding 生成失败需保证流程不中断并可追溯。
    方案：捕获异常并返回包含错误信息的 `Data`。
    代价：错误路径下输出向量为空，需下游处理。
    重评：当引入强制失败或重试机制时。
    """
    display_name: str = "Text Embedder"
    description: str = "Generate embeddings for a given message using the specified embedding model."
    icon = "binary"
    legacy: bool = True
    replacement = ["models.EmbeddingModel"]
    inputs = [
        HandleInput(
            name="embedding_model",
            display_name="Embedding Model",
            info="The embedding model to use for generating embeddings.",
            input_types=["Embeddings"],
            required=True,
        ),
        MessageInput(
            name="message",
            display_name="Message",
            info="The message to generate embeddings for.",
            required=True,
        ),
    ]
    outputs = [
        Output(display_name="Embedding Data", name="embeddings", method="generate_embeddings"),
    ]

    def generate_embeddings(self) -> Data:
        """生成文本 embedding 并返回 `Data`。

        契约：返回 `Data`，包含原始文本与向量；失败时包含 `error` 字段。
        关键路径：校验 -> 调用模型 -> 组装输出。
        异常流：任何异常捕获并记录日志，返回错误 `Data`。
        决策：
        问题：上游输入可能为空或模型不兼容。
        方案：集中校验并在异常时返回错误信息。
        代价：错误场景返回空向量，可能影响下游计算。
        重评：当需要严格失败或重试策略时。
        """
        try:
            embedding_model: Embeddings = self.embedding_model
            message: Message = self.message

            # 注意：统一校验 embedding 模型是否具备 `embed_documents` 方法。
            if not embedding_model or not hasattr(embedding_model, "embed_documents"):
                msg = "Invalid or incompatible embedding model"
                raise ValueError(msg)

            text_content = message.text if message and message.text else ""
            if not text_content:
                msg = "No text content found in message"
                raise ValueError(msg)

            embeddings = embedding_model.embed_documents([text_content])
            if not embeddings or not isinstance(embeddings, list):
                msg = "Invalid embeddings generated"
                raise ValueError(msg)

            embedding_vector = embeddings[0]
            self.status = {"text": text_content, "embeddings": embedding_vector}
            return Data(data={"text": text_content, "embeddings": embedding_vector})
        except Exception as e:  # noqa: BLE001
            logger.exception("Error generating embeddings")
            error_data = Data(data={"text": "", "embeddings": [], "error": str(e)})
            self.status = {"error": str(e)}
            return error_data
