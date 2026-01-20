"""
模块名称：JSON Document 构建器（已停用）

本模块提供将 `Document.page_content` 包装为 JSON 并输出新 `Document` 的能力，主要用于旧流程中的结构化输出。主要功能包括：
- 使用指定 `key` 包装 `Document` 的 `page_content`
- 支持单个或列表 `Document` 的批量处理

关键组件：
- `JSONDocumentBuilder`：构建器组件

设计背景：历史组件用于在索引前统一文档结构。
注意事项：输出 `page_content` 为 JSON 字符串；输入类型不符会抛 `TypeError`。
"""

import orjson
from langchain_core.documents import Document

from lfx.custom.custom_component.custom_component import CustomComponent
from lfx.io import HandleInput, StrInput


class JSONDocumentBuilder(CustomComponent):
    """JSON Document 构建组件。

    契约：输入 `Document` 或其列表，输出 JSON 包装后的 `Document`/列表。
    失败语义：输入类型不正确时抛 `TypeError`。
    副作用：更新组件 `repr_value`。
    """

    display_name: str = "JSON Document Builder"
    description: str = "Build a Document containing a JSON object using a key and another Document page content."
    name = "JSONDocumentBuilder"
    documentation: str = "https://docs.langflow.org/legacy-core-components"
    legacy = True

    inputs = [
        StrInput(
            name="key",
            display_name="Key",
            required=True,
        ),
        HandleInput(
            name="document",
            display_name="Document",
            required=True,
        ),
    ]

    def build(
        self,
        key: str,
        document: Document,
    ) -> Document:
        """构建 JSON 格式的 `Document`。

        契约：`page_content` 被包装为 `{key: 原内容}` 的 JSON 字符串。
        失败语义：输入不是 `Document` 或 `list[Document]` 时抛 `TypeError`。
        副作用：更新组件 `repr_value`。

        关键路径（三步）：
        1) 判断输入为单个还是列表
        2) 生成 JSON 字符串并构造新 `Document`
        3) 缓存结果并返回
        """
        documents = None
        if isinstance(document, list):
            documents = [Document(page_content=orjson.dumps({key: doc.page_content}).decode()) for doc in document]
        elif isinstance(document, Document):
            documents = Document(page_content=orjson.dumps({key: document.page_content}).decode())
        else:
            msg = f"Expected Document or list of Documents, got {type(document)}"
            raise TypeError(msg)

        self.repr_value = documents
        return documents
