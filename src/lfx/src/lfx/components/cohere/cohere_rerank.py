"""
模块名称：cohere_rerank

本模块提供 Cohere Rerank 组件封装，用于文档重排。
主要功能包括：
- 构建 Cohere Rerank 压缩器
- 暴露模型与 top_n 参数

关键组件：
- `CohereRerankComponent`：重排组件

设计背景：检索结果需要基于语义相关性重新排序
使用场景：RAG 管道的重排阶段
注意事项：需安装 `langchain-cohere`
"""

from lfx.base.compressors.model import LCCompressorComponent
from lfx.field_typing import BaseDocumentCompressor
from lfx.inputs.inputs import SecretStrInput
from lfx.io import DropdownInput
from lfx.template.field.base import Output


class CohereRerankComponent(LCCompressorComponent):
    """Cohere 重排组件。

    契约：需提供 `api_key` 并选择模型。
    副作用：导入并创建 CohereRerank 实例。
    失败语义：缺少依赖时抛 `ImportError`。
    """
    display_name = "Cohere Rerank"
    description = "Rerank documents using the Cohere API."
    name = "CohereRerank"
    icon = "Cohere"

    inputs = [
        *LCCompressorComponent.inputs,
        SecretStrInput(
            name="api_key",
            display_name="Cohere API Key",
        ),
        DropdownInput(
            name="model",
            display_name="Model",
            options=[
                "rerank-english-v3.0",
                "rerank-multilingual-v3.0",
                "rerank-english-v2.0",
                "rerank-multilingual-v2.0",
            ],
            value="rerank-english-v3.0",
        ),
    ]

    outputs = [
        Output(
            display_name="Reranked Documents",
            name="reranked_documents",
            method="compress_documents",
        ),
    ]

    def build_compressor(self) -> BaseDocumentCompressor:  # type: ignore[type-var]
        """构建 Cohere Rerank 压缩器。

        契约：`model` 必须在支持列表中。
        副作用：动态导入 `langchain_cohere` 并实例化。
        失败语义：未安装依赖则抛 `ImportError`。
        决策：在函数内延迟导入。
        问题：避免无该依赖的环境在加载组件时直接失败。
        方案：运行时导入并给出明确安装提示。
        代价：首次调用时才暴露依赖问题。
        重评：当依赖变为强制安装时。
        """
        try:
            from langchain_cohere import CohereRerank
        except ImportError as e:
            msg = "Please install langchain-cohere to use the Cohere model."
            raise ImportError(msg) from e
        return CohereRerank(
            cohere_api_key=self.api_key,
            model=self.model,
            top_n=self.top_n,
        )
