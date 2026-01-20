"""
模块名称：文本向量化组件（已停用）

本模块提供对文本列表的向量化能力，主要用于旧流程中将文本转换为向量 `Data`。主要功能包括：
- 使用 `Embeddings` 对文本列表进行向量化

关键组件：
- `EmbedComponent`：向量化组件

设计背景：历史组件命名沿用早期接口，保留向后兼容。
注意事项：字段名 `embbedings` 为历史拼写错误，保持兼容不做修正。
"""

from lfx.custom.custom_component.custom_component import CustomComponent
from lfx.field_typing import Embeddings
from lfx.schema.data import Data


class EmbedComponent(CustomComponent):
    """文本向量化组件。

    契约：`texts` 为字符串列表，`embbedings` 提供 `embed_documents` 方法。
    失败语义：嵌入调用失败由底层抛出异常。
    副作用：更新组件 `status`。
    """

    display_name = "Embed Texts"
    name = "Embed"

    def build_config(self):
        """返回输入配置。

        契约：字段名保持历史拼写，避免破坏旧流程。
        失败语义：无。
        副作用：无。
        """
        return {"texts": {"display_name": "Texts"}, "embbedings": {"display_name": "Embeddings"}}

    def build(self, texts: list[str], embbedings: Embeddings) -> Data:
        """执行文本向量化并返回 `Data`。

        契约：输出 `Data.vector` 为嵌入向量列表。
        失败语义：嵌入失败时抛异常。
        副作用：更新组件 `status`。
        """
        vectors = Data(vector=embbedings.embed_documents(texts))
        self.status = vectors
        return vectors
