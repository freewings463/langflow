"""
模块名称：similarity

本模块提供 embedding 相似度计算组件，支持多种距离度量。
主要功能包括：
- 功能1：计算两条 embedding 的余弦相似度。
- 功能2：计算欧氏距离与曼哈顿距离。

使用场景：对比两条向量的相似度或距离，用于检索/聚类评估。
关键组件：
- 类 `EmbeddingSimilarityComponent`

设计背景：提供轻量级向量相似度计算，便于在流程内验证 embedding 质量。
注意事项：输入必须包含且仅包含两条 embedding。
"""

from typing import Any

import numpy as np

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, DropdownInput, Output
from lfx.schema.data import Data


class EmbeddingSimilarityComponent(Component):
    """Embedding 相似度计算组件。

    契约：输入为包含两条 `Data` 的列表；输出为 `Data`，包含相似度/距离结果。
    关键路径：
    1) 校验输入数量与维度一致性；
    2) 按选择的度量计算分数；
    3) 返回封装后的 `Data`。
    异常流：输入数量不为 2 时抛 `ValueError`。
    决策：
    问题：组件内需要统一输出格式以便下游消费。
    方案：将原始 embedding 与分数一起封装到 `Data`。
    代价：输出包含原始向量，数据量可能较大。
    重评：当下游仅需分数或向量过大时。
    """
    display_name: str = "Embedding Similarity"
    description: str = "Compute selected form of similarity between two embedding vectors."
    icon = "equal"
    legacy: bool = True
    replacement = ["datastax.AstraDB"]

    inputs = [
        DataInput(
            name="embedding_vectors",
            display_name="Embedding Vectors",
            info="A list containing exactly two data objects with embedding vectors to compare.",
            is_list=True,
            required=True,
        ),
        DropdownInput(
            name="similarity_metric",
            display_name="Similarity Metric",
            info="Select the similarity metric to use.",
            options=["Cosine Similarity", "Euclidean Distance", "Manhattan Distance"],
            value="Cosine Similarity",
        ),
    ]

    outputs = [
        Output(display_name="Similarity Data", name="similarity_data", method="compute_similarity"),
    ]

    def compute_similarity(self) -> Data:
        """计算相似度/距离并返回 `Data`。

        契约：输入列表长度必须为 2；embedding 维度需一致。
        关键路径：解析向量 -> 按 metric 计算 -> 组装 `Data`。
        异常流：维度不一致返回错误字段；数量不匹配抛异常。
        决策：
        问题：不同度量需要统一输出结构。
        方案：返回 `similarity_score` 字段并保留原始向量。
        代价：包含完整 embedding 会增加内存占用。
        重评：当需要输出更紧凑结果时。
        """
        embedding_vectors: list[Data] = self.embedding_vectors

        # 注意：仅支持两条向量的两两对比。
        if len(embedding_vectors) != 2:  # noqa: PLR2004
            msg = "Exactly two embedding vectors are required."
            raise ValueError(msg)

        embedding_1 = np.array(embedding_vectors[0].data["embeddings"])
        embedding_2 = np.array(embedding_vectors[1].data["embeddings"])

        if embedding_1.shape != embedding_2.shape:
            similarity_score: dict[str, Any] = {"error": "Embeddings must have the same dimensions."}
        else:
            similarity_metric = self.similarity_metric

            if similarity_metric == "Cosine Similarity":
                score = np.dot(embedding_1, embedding_2) / (np.linalg.norm(embedding_1) * np.linalg.norm(embedding_2))
                similarity_score = {"cosine_similarity": score}

            elif similarity_metric == "Euclidean Distance":
                score = np.linalg.norm(embedding_1 - embedding_2)
                similarity_score = {"euclidean_distance": score}

            elif similarity_metric == "Manhattan Distance":
                score = np.sum(np.abs(embedding_1 - embedding_2))
                similarity_score = {"manhattan_distance": score}

        # 实现：将向量与分数一并封装，便于下游追溯。
        similarity_data = Data(
            data={
                "embedding_1": embedding_vectors[0].data["embeddings"],
                "embedding_2": embedding_vectors[1].data["embeddings"],
                "similarity_score": similarity_score,
            },
            text_key="similarity_score",
        )

        self.status = similarity_data
        return similarity_data
