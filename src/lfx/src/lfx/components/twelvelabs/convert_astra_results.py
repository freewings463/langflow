"""
模块名称：Astra 结果转换为 TwelveLabs 输入

本模块将 Astra DB 检索结果转换为 TwelveLabs Pegasus 可用的 `index_id` 与 `video_id`。
主要功能包括：
- 解析 `Data.metadata`（支持嵌套 `metadata` 结构）
- 提取 `index_id` 与 `video_id` 并输出为 `Message`

关键组件：
- `ConvertAstraToTwelveLabs`

设计背景：Astra DB 返回结构与 TwelveLabs Pegasus 输入不一致，需要桥接层。
注意事项：未找到字段时输出空字符串。
"""

from typing import Any

from lfx.custom import Component
from lfx.io import HandleInput, Output
from lfx.schema import Data
from lfx.schema.message import Message


class ConvertAstraToTwelveLabs(Component):
    """Astra 结果到 TwelveLabs 输入的转换组件。

    契约：
    - 输入：`astra_results`（`Data` 或其列表）
    - 输出：`index_id` / `video_id` 两个 `Message`
    - 副作用：无外部调用，仅解析输入数据
    - 失败语义：输入为空时保持空输出，不抛异常
    """

    display_name = "Convert Astra DB to Pegasus Input"
    description = "Converts Astra DB search results to inputs compatible with TwelveLabs Pegasus."
    icon = "TwelveLabs"
    name = "ConvertAstraToTwelveLabs"
    documentation = "https://github.com/twelvelabs-io/twelvelabs-developer-experience/blob/main/integrations/Langflow/TWELVE_LABS_COMPONENTS_README.md"

    inputs = [
        HandleInput(
            name="astra_results",
            display_name="Astra DB Results",
            input_types=["Data"],
            info="Search results from Astra DB component",
            required=True,
            is_list=True,
        )
    ]

    outputs = [
        Output(
            name="index_id",
            display_name="Index ID",
            type_=Message,
            method="get_index_id",
        ),
        Output(
            name="video_id",
            display_name="Video ID",
            type_=Message,
            method="get_video_id",
        ),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._video_id = None
        self._index_id = None

    def build(self, **kwargs: Any) -> None:  # noqa: ARG002 - 父类兼容要求
        """解析 Astra DB 结果并提取 TwelveLabs 索引信息。

        契约：
        - 输入：`astra_results`（`Data` 或列表）
        - 输出：内部缓存 `_index_id` / `_video_id`
        - 副作用：修改组件状态字段
        - 失败语义：输入为空时直接返回，不抛异常

        关键路径（三步）：
        1) 将输入规整为列表
        2) 逐条读取 `metadata`（支持嵌套结构）
        3) 命中 `index_id` 与 `video_id` 后提前停止
        """
        if not self.astra_results:
            return

        # 注意：单条输入需包装成列表
        results = self.astra_results if isinstance(self.astra_results, list) else [self.astra_results]

        # 注意：从 metadata 中提取索引信息
        for doc in results:
            if not isinstance(doc, Data):
                continue

            # 兼容嵌套 metadata 结构
            metadata = {}
            if hasattr(doc, "metadata") and isinstance(doc.metadata, dict):
                # 优先读取嵌套字段
                metadata = doc.metadata.get("metadata", doc.metadata)

            # 提取 index_id 与 video_id
            self._index_id = metadata.get("index_id")
            self._video_id = metadata.get("video_id")

            # 两者均已命中则提前结束
            if self._index_id and self._video_id:
                break

    def get_video_id(self) -> Message:
        """返回提取到的 `video_id`（未命中则为空字符串）。

        契约：
        - 输入：无（依赖内部缓存）
        - 输出：`Message`
        - 副作用：触发 `build()` 以刷新缓存
        - 失败语义：未命中时返回空字符串
        """
        self.build()
        return Message(text=self._video_id if self._video_id else "")

    def get_index_id(self) -> Message:
        """返回提取到的 `index_id`（未命中则为空字符串）。

        契约：
        - 输入：无（依赖内部缓存）
        - 输出：`Message`
        - 副作用：触发 `build()` 以刷新缓存
        - 失败语义：未命中时返回空字符串
        """
        self.build()
        return Message(text=self._index_id if self._index_id else "")
