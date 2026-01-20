"""
模块名称：MultiQueryRetriever 组件（已停用）

本模块提供基于 LLM 的多查询检索器构建能力，主要用于通过多视角问题提高召回。主要功能包括：
- 使用默认或自定义 Prompt 构建 `MultiQueryRetriever`

关键组件：
- `MultiQueryRetrieverComponent`：多查询检索组件

设计背景：历史上用于增强检索召回率，现标记为 legacy。
注意事项：依赖 LangChain `MultiQueryRetriever`。
"""

from langchain.prompts import PromptTemplate
from langchain.retrievers import MultiQueryRetriever

from lfx.custom.custom_component.custom_component import CustomComponent
from lfx.field_typing import BaseRetriever, LanguageModel, Text
from lfx.inputs.inputs import HandleInput, StrInput


class MultiQueryRetrieverComponent(CustomComponent):
    """多查询检索器组件。

    契约：必须提供 `llm` 与 `retriever`。
    失败语义：输入类型不兼容时由底层 LangChain 抛异常。
    副作用：无。
    """
    display_name = "MultiQueryRetriever"
    description = "Initialize from llm using default template."
    documentation = "https://python.langchain.com/docs/modules/data_connection/retrievers/how_to/MultiQueryRetriever"
    name = "MultiQueryRetriever"
    legacy = True

    inputs = [
        HandleInput(
            name="llm",
            display_name="LLM",
            input_types=["LanguageModel"],
            required=True,
        ),
        HandleInput(
            name="retriever",
            display_name="Retriever",
            input_types=["BaseRetriever"],
            required=True,
        ),
        StrInput(
            name="prompt",
            display_name="Prompt",
            value="You are an AI language model assistant. Your task is \n"
            "to generate 3 different versions of the given user \n"
            "question to retrieve relevant documents from a vector database. \n"
            "By generating multiple perspectives on the user question, \n"
            "your goal is to help the user overcome some of the limitations \n"
            "of distance-based similarity search. Provide these alternative \n"
            "questions separated by newlines. Original question: {question}",
            required=False,
        ),
        StrInput(
            name="parser_key",
            display_name="Parser Key",
            value="lines",
            required=False,
        ),
    ]

    def build(
        self,
        llm: LanguageModel,
        retriever: BaseRetriever,
        prompt: Text | None = None,
        parser_key: str = "lines",
    ) -> MultiQueryRetriever:
        """构建 `MultiQueryRetriever`。

        契约：`prompt` 为空时使用默认模板。
        失败语义：模板构造或检索器创建失败时抛异常。
        副作用：无。

        关键路径（三步）：
        1) 判断是否使用自定义 Prompt
        2) 构造 `PromptTemplate`（如需）
        3) 创建并返回 `MultiQueryRetriever`
        """
        if not prompt:
            return MultiQueryRetriever.from_llm(llm=llm, retriever=retriever, parser_key=parser_key)
        prompt_template = PromptTemplate.from_template(prompt)
        return MultiQueryRetriever.from_llm(llm=llm, retriever=retriever, prompt=prompt_template, parser_key=parser_key)
