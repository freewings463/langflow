"""模块名称：自查询检索器组件

本模块封装 LangChain `SelfQueryRetriever`，通过 LLM 自动生成向量检索过滤条件。
主要功能包括：解析字段元信息、构建自查询检索器、返回检索结果。

关键组件：
- `SelfQueryRetrieverComponent`：自查询检索器组件入口

设计背景：让自然语言查询自动转化为结构化检索。
注意事项：`attribute_infos` 需提供字段元信息，否则检索条件不完整。
"""

from langchain.chains.query_constructor.base import AttributeInfo
from langchain.retrievers.self_query.base import SelfQueryRetriever

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import HandleInput, MessageTextInput
from lfx.io import Output
from lfx.schema.data import Data
from lfx.schema.message import Message


class SelfQueryRetrieverComponent(Component):
    """自查询检索器组件。

    契约：输入 `query/vectorstore/attribute_infos/document_content_description/llm`；
    输出 `list[Data]`；副作用：更新 `self.status`；失败语义：不支持的 `query` 类型抛 `TypeError`。
    关键路径：1) 生成 `AttributeInfo` 2) 构建检索器 3) 执行检索并转换结果。
    决策：启用 `enable_limit=True`
    问题：避免检索结果过量导致成本上升
    方案：由检索器自动施加结果上限
    代价：可能漏掉长尾结果
    重评：当召回不足时关闭或提高限制
    """
    display_name = "Self Query Retriever"
    description = "Retriever that uses a vector store and an LLM to generate the vector store queries."
    name = "SelfQueryRetriever"
    icon = "LangChain"
    legacy: bool = True

    inputs = [
        HandleInput(
            name="query",
            display_name="Query",
            info="Query to be passed as input.",
            input_types=["Message"],
        ),
        HandleInput(
            name="vectorstore",
            display_name="Vector Store",
            info="Vector Store to be passed as input.",
            input_types=["VectorStore"],
        ),
        HandleInput(
            name="attribute_infos",
            display_name="Metadata Field Info",
            info="Metadata Field Info to be passed as input.",
            input_types=["Data"],
            is_list=True,
        ),
        MessageTextInput(
            name="document_content_description",
            display_name="Document Content Description",
            info="Document Content Description to be passed as input.",
        ),
        HandleInput(
            name="llm",
            display_name="LLM",
            info="LLM to be passed as input.",
            input_types=["LanguageModel"],
        ),
    ]

    outputs = [
        Output(
            display_name="Retrieved Documents",
            name="documents",
            method="retrieve_documents",
        ),
    ]

    def retrieve_documents(self) -> list[Data]:
        """执行自查询检索并返回 `Data` 列表。

        关键路径（三步）：
        1) 构建 `AttributeInfo` 与检索器
        2) 解析 `query` 输入类型
        3) 调用检索并转换为 `Data`

        异常流：`query` 类型不支持抛 `TypeError`；检索异常透传。
        排障入口：`self.status` 保存返回的 `Data` 列表。
        决策：优先支持 `Message` 与 `str`
        问题：上游可能传入消息或纯文本
        方案：按类型分支提取文本
        代价：其他类型需要额外适配
        重评：当新增输入类型时扩展分支
        """
        metadata_field_infos = [AttributeInfo(**value.data) for value in self.attribute_infos]
        self_query_retriever = SelfQueryRetriever.from_llm(
            llm=self.llm,
            vectorstore=self.vectorstore,
            document_contents=self.document_content_description,
            metadata_field_info=metadata_field_infos,
            enable_limit=True,
        )

        if isinstance(self.query, Message):
            input_text = self.query.text
        elif isinstance(self.query, str):
            input_text = self.query
        else:
            msg = f"Query type {type(self.query)} not supported."
            raise TypeError(msg)

        documents = self_query_retriever.invoke(input=input_text, config={"callbacks": self.get_langchain_callbacks()})
        data = [Data.from_document(document) for document in documents]
        self.status = data
        return data
