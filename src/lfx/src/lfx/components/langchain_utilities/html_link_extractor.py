"""模块名称：HTML 链接提取组件

本模块封装 LangChain 的 HTML 链接提取器，用于将 HTML 内容中的超链接抽取为文档。
主要功能包括：设置边类型、处理片段、并输出 `BaseDocumentTransformer`。

关键组件：
- `HtmlLinkExtractorComponent`：链接提取器的组件化适配层

设计背景：在图谱/检索流程中统一链接抽取方式。
注意事项：`drop_fragments=True` 会移除 URL 片段。
"""

from typing import Any

from langchain_community.graph_vectorstores.extractors import HtmlLinkExtractor, LinkExtractorTransformer
from langchain_core.documents import BaseDocumentTransformer

from lfx.base.document_transformers.model import LCDocumentTransformerComponent
from lfx.inputs.inputs import BoolInput, DataInput, StrInput


class HtmlLinkExtractorComponent(LCDocumentTransformerComponent):
    """HTML 链接提取组件。

    契约：输入 `data_input/kind/drop_fragments`；输出 `BaseDocumentTransformer`；副作用无；
    失败语义：HTML 解析失败将由底层提取器处理并可能返回空结果。
    关键路径：1) 读取配置 2) 构建 `HtmlLinkExtractor` 3) 包装为 transformer。
    决策：默认 `kind=hyperlink`
    问题：图谱边类型需要稳定语义
    方案：使用通用 `hyperlink` 标记
    代价：无法区分更细粒度关系
    重评：当引入多类型边时改为用户必填
    """
    display_name = "HTML Link Extractor"
    description = "Extract hyperlinks from HTML content."
    documentation = "https://python.langchain.com/v0.2/api_reference/community/graph_vectorstores/langchain_community.graph_vectorstores.extractors.html_link_extractor.HtmlLinkExtractor.html"
    name = "HtmlLinkExtractor"
    icon = "LangChain"

    inputs = [
        StrInput(name="kind", display_name="Kind of edge", value="hyperlink", required=False),
        BoolInput(name="drop_fragments", display_name="Drop URL fragments", value=True, required=False),
        DataInput(
            name="data_input",
            display_name="Input",
            info="The texts from which to extract links.",
            input_types=["Document", "Data"],
            required=True,
        ),
    ]

    def get_data_input(self) -> Any:
        """提供文档转换器所需的输入数据。

        契约：输入无；输出 `data_input`；副作用无；失败语义：未设置时返回 `None`。
        关键路径：1) 原样返回字段。
        决策：不在此处做格式化
        问题：避免提前丢失 `Document` 元数据
        方案：保持原对象引用
        代价：调用方需自行校验类型
        重评：当统一数据校验层上线后下沉校验
        """
        return self.data_input

    def build_document_transformer(self) -> BaseDocumentTransformer:
        """构建链接提取的文档转换器。

        契约：输入 `kind/drop_fragments`；输出 `BaseDocumentTransformer`；副作用无；
        失败语义：参数不合法时由底层抛错。
        关键路径：1) 构造 extractor 2) 转为 document extractor 3) 包装为 transformer。
        决策：使用 `LinkExtractorTransformer`
        问题：需要标准化输出为 LangChain 文档流
        方案：使用官方 transformer 包装
        代价：额外一次对象创建
        重评：当下游可直接消费 extractor 时移除包装
        """
        return LinkExtractorTransformer(
            [HtmlLinkExtractor(kind=self.kind, drop_fragments=self.drop_fragments).as_document_extractor()]
        )
