"""
模块名称：TwelveLabs 视频向量组件

本模块封装 TwelveLabs 视频向量接口，支持生成视频级或片段级向量。
主要功能包括：
- 上传视频并轮询向量任务状态
- 优先返回视频级向量，必要时回退到片段向量

关键组件：
- `TwelveLabsVideoEmbeddings`
- `TwelveLabsVideoEmbeddingsComponent`

设计背景：为视频检索提供统一 `Embeddings` 接口实现。
注意事项：任务轮询间隔固定 5 秒；缺少向量时抛异常。
"""

import time
from pathlib import Path
from typing import Any, cast

from twelvelabs import TwelveLabs

from lfx.base.embeddings.model import LCEmbeddingsModel
from lfx.field_typing import Embeddings
from lfx.io import DropdownInput, IntInput, SecretStrInput


class TwelveLabsVideoEmbeddings(Embeddings):
    """TwelveLabs 视频向量实现。

    契约：
    - 输入：视频路径列表或单条路径
    - 输出：向量列表或单向量
    - 副作用：上传视频并调用 TwelveLabs 向量任务
    - 失败语义：无向量结果时抛 `ValueError`
    """

    def __init__(self, api_key: str, model_name: str = "Marengo-retrieval-2.7") -> None:
        self.client = TwelveLabs(api_key=api_key)
        self.model_name = model_name

    def _wait_for_task_completion(self, task_id: str) -> Any:
        """轮询任务直到完成。

        契约：
        - 输入：`task_id`
        - 输出：任务结果对象
        - 副作用：轮询 TwelveLabs API
        - 失败语义：API 异常向上传递
        """
        while True:
            result = self.client.embed.task.retrieve(id=task_id)
            if result.status == "ready":
                return result
            time.sleep(5)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """为视频列表生成向量。

        契约：
        - 输入：视频路径列表
        - 输出：二维向量列表
        - 副作用：上传视频并轮询任务
        - 失败语义：无向量结果时抛 `ValueError`
        """
        embeddings: list[list[float]] = []
        for text in texts:
            video_path = text.page_content if hasattr(text, "page_content") else str(text)
            result = self.embed_video(video_path)

            # 注意：优先取视频向量，必要时回退片段向量
            if result["video_embedding"] is not None:
                embeddings.append(cast("list[float]", result["video_embedding"]))
            elif result["clip_embeddings"] and len(result["clip_embeddings"]) > 0:
                embeddings.append(cast("list[float]", result["clip_embeddings"][0]))
            else:
                # 注意：两类向量都缺失则抛异常
                error_msg = "No embeddings were generated for the video"
                raise ValueError(error_msg)

        return embeddings

    def embed_query(self, text: str) -> list[float]:
        """为单个视频生成向量。

        契约：
        - 输入：视频路径
        - 输出：向量列表
        - 副作用：上传视频并轮询任务
        - 失败语义：无向量结果时抛 `ValueError`
        """
        video_path = text.page_content if hasattr(text, "page_content") else str(text)
        result = self.embed_video(video_path)

        # 注意：优先取视频向量，必要时回退片段向量
        if result["video_embedding"] is not None:
            return cast("list[float]", result["video_embedding"])
        if result["clip_embeddings"] and len(result["clip_embeddings"]) > 0:
            return cast("list[float]", result["clip_embeddings"][0])
        # 注意：两类向量都缺失则抛异常
        error_msg = "No embeddings were generated for the video"
        raise ValueError(error_msg)

    def embed_video(self, video_path: str) -> dict[str, list[float] | list[list[float]]]:
        """上传视频并返回向量结果字典。

        契约：
        - 输入：视频路径
        - 输出：包含视频级/片段级向量的字典
        - 副作用：上传视频并创建向量任务
        - 失败语义：API 异常向上传递
        """
        file_path = Path(video_path)
        with file_path.open("rb") as video_file:
            task = self.client.embed.task.create(
                model_name=self.model_name,
                video_file=video_file,
                video_embedding_scopes=["video", "clip"],
            )

        result = self._wait_for_task_completion(task.id)

        video_embedding: dict[str, list[float] | list[list[float]]] = {
            "video_embedding": [],  # 注意：统一用空列表表示缺失
            "clip_embeddings": [],
        }

        if hasattr(result.video_embedding, "segments") and result.video_embedding.segments:
            for seg in result.video_embedding.segments:
                # 注意：仅采集 `embeddings_float` 且 scope=video 的向量
                if hasattr(seg, "embeddings_float") and seg.embedding_scope == "video":
                    # 转为 float 列表
                    video_embedding["video_embedding"] = [float(x) for x in seg.embeddings_float]

        return video_embedding


class TwelveLabsVideoEmbeddingsComponent(LCEmbeddingsModel):
    """TwelveLabs 视频向量组件。

    契约：
    - 输入：API Key 与模型名
    - 输出：`Embeddings` 实例
    - 副作用：构造 TwelveLabs 客户端
    - 失败语义：构造异常向上传递
    """

    display_name = "TwelveLabs Video Embeddings"
    description = "Generate embeddings from videos using TwelveLabs video embedding models."
    name = "TwelveLabsVideoEmbeddings"
    icon = "TwelveLabs"
    documentation = "https://github.com/twelvelabs-io/twelvelabs-developer-experience/blob/main/integrations/Langflow/TWELVE_LABS_COMPONENTS_README.md"
    inputs = [
        SecretStrInput(name="api_key", display_name="TwelveLabs API Key", required=True),
        DropdownInput(
            name="model_name",
            display_name="Model",
            advanced=False,
            options=["Marengo-retrieval-2.7"],
            value="Marengo-retrieval-2.7",
        ),
        IntInput(name="request_timeout", display_name="Request Timeout", advanced=True),
    ]

    def build_embeddings(self) -> Embeddings:
        """构建 TwelveLabs 视频向量客户端。

        契约：
        - 输入：API Key 与模型名
        - 输出：`Embeddings` 实例
        - 副作用：构造 TwelveLabs 客户端
        - 失败语义：构造异常向上传递
        """
        return TwelveLabsVideoEmbeddings(api_key=self.api_key, model_name=self.model_name)
