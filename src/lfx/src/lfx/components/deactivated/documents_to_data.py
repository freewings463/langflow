"""
模块名称：Document 转 Data 组件（已停用）

本模块提供将 LangChain `Document` 转换为 Langflow `Data` 的能力，主要用于在旧流程中统一数据结构。主要功能包括：
- 将单个或列表 `Document` 转换为 `Data`

关键组件：
- `DocumentsToDataComponent`：转换组件

设计背景：兼容旧版组件输出与 Langflow 数据结构。
注意事项：若输入为单个 `Document` 将自动包装为列表。
"""

from langchain_core.documents import Document

from lfx.custom.custom_component.custom_component import CustomComponent
from lfx.schema.data import Data


class DocumentsToDataComponent(CustomComponent):
    """Document 转 Data 组件。

    契约：输入为 `Document` 或其列表，输出为 `Data` 列表。
    失败语义：输入类型不匹配时可能抛异常。
    副作用：更新组件 `status`。
    """

    display_name = "Documents ⇢ Data"
    description = "Convert LangChain Documents into Data."
    icon = "LangChain"
    name = "DocumentsToData"

    field_config = {
        "documents": {"display_name": "Documents"},
    }

    def build(self, documents: list[Document]) -> list[Data]:
        """执行转换并返回 `Data` 列表。

        契约：单个 `Document` 自动包装为列表。
        失败语义：输入不是 `Document` 时由上层类型检查处理。
        副作用：更新组件 `status`。
        """
        if isinstance(documents, Document):
            documents = [documents]
        data = [Data.from_document(document) for document in documents]
        self.status = data
        return data
