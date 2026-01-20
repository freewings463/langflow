"""
模块名称：文本分割器基础组件模型

本模块定义文本分割器组件的最小契约，用于接入 LangChain 文档转换体系。
主要功能包括：
- 固定 `trace_type` 为 `text_splitter` 以统一追踪分类
- 约束输出端为 `text_splitter`，并校验实现完整性
- 提供与 `BaseDocumentTransformer` 的桥接构建入口

关键组件：
- LCTextSplitterComponent：文本分割器基类，负责输出契约与构建接口

设计背景：统一文本分割器接口，便于组件注册与下游依赖。
注意事项：子类必须实现 `build_text_splitter`，否则无法构建文档转换器。
"""

from abc import abstractmethod

from langchain_core.documents import BaseDocumentTransformer
from langchain_text_splitters import TextSplitter

from lfx.base.document_transformers.model import LCDocumentTransformerComponent


class LCTextSplitterComponent(LCDocumentTransformerComponent):
    """文本分割器组件的基础抽象。

    契约：子类需实现 `build_text_splitter` 并暴露名为 `text_splitter` 的输出端。
    副作用：无；仅依赖实例上的 `outputs` 与方法定义。
    失败语义：缺少输出或方法时抛 `ValueError`，调用方应在组件注册阶段修复。
    """

    trace_type = "text_splitter"

    def _validate_outputs(self) -> None:
        """校验文本分割器输出契约是否完整。

        输入：无（读取 `self.outputs` 与实例方法）。
        输出：无；仅在失败时抛错。
        失败语义：当缺少 `text_splitter` 输出或方法时抛 `ValueError`。
        """
        required_output_methods = ["text_splitter"]
        output_names = [output.name for output in self.outputs]
        for method_name in required_output_methods:
            if method_name not in output_names:
                msg = f"Output with name '{method_name}' must be defined."
                raise ValueError(msg)
            if not hasattr(self, method_name):
                msg = f"Method '{method_name}' must be defined."
                raise ValueError(msg)

    def build_document_transformer(self) -> BaseDocumentTransformer:
        """构建文档转换器，复用文本分割器实现。

        输入：无。
        输出：`BaseDocumentTransformer`，由 `build_text_splitter` 生成。
        失败语义：子类未实现 `build_text_splitter` 时抛 `TypeError`。
        """
        return self.build_text_splitter()

    @abstractmethod
    def build_text_splitter(self) -> TextSplitter:
        """构建文本分割器。

        输入：无。
        输出：`TextSplitter` 实例。
        失败语义：由子类实现决定，错误原样透传。
        """
