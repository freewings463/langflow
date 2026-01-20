"""
模块名称：向量检索 `RAG` 示例图

本模块提供“文档入库 + 检索问答”的 `RAG` 组合示例，用于展示向量化检索流程。主要功能包括：
- 文档切分并写入 `AstraDB` 向量库
- 基于用户问题检索并生成回答

关键组件：
- `ingestion_graph`: 文档入库图
- `rag_graph`: 检索问答图
- `vector_store_rag_graph`: 组合入库与检索图

设计背景：`RAG` 是常见入门场景，需要同时展示入库与检索两段流程。
注意事项：运行时需配置向量库与嵌入模型凭据。
"""

from textwrap import dedent

from lfx.components.data import FileComponent
from lfx.components.datastax import AstraDBVectorStoreComponent
from lfx.components.input_output import ChatInput, ChatOutput
from lfx.components.models import LanguageModelComponent
from lfx.components.models_and_agents import PromptComponent
from lfx.components.openai.openai import OpenAIEmbeddingsComponent
from lfx.components.processing import ParserComponent
from lfx.components.processing.split_text import SplitTextComponent
from lfx.graph import Graph


def ingestion_graph():
    """构建文档入库子图。

    契约：返回入库 `Graph`，输入为文件内容，输出为向量库写入结果。
    副作用：构图阶段无 `I/O`；运行时读取文件并写入向量库。
    失败语义：文件读取、嵌入生成或向量库写入失败会在执行期抛错。
    关键路径：1) 读取文件 2) 文本切分 3) 生成嵌入并写入向量库。
    决策：使用 `SplitTextComponent` 进行切分
    问题：大文档直接嵌入会超出模型限制
    方案：先切分再嵌入并写入
    代价：切分策略会影响召回质量
    重评：当文档规模或召回要求变化时调整切分策略
    """
    file_component = FileComponent()
    text_splitter = SplitTextComponent()
    text_splitter.set(data_inputs=file_component.load_files_message)
    openai_embeddings = OpenAIEmbeddingsComponent()
    vector_store = AstraDBVectorStoreComponent()
    vector_store.set(
        embedding_model=openai_embeddings.build_embeddings,
        ingest_data=text_splitter.split_text,
    )

    return Graph(file_component, vector_store)


def rag_graph():
    """构建检索问答子图。

    契约：返回检索 `Graph`，输入为问题文本，输出为模型回答。
    副作用：构图阶段无 `I/O`；运行时进行向量检索与模型推理。
    失败语义：向量检索或模型调用失败会在执行期抛错。
    关键路径：1) 向量检索 2) 解析上下文 3) 生成回答。
    决策：检索结果统一通过 `ParserComponent` 拼接为上下文
    问题：模型需要可读的上下文格式
    方案：将检索结果合并为纯文本再注入提示
    代价：上下文过长时可能触发模型输入限制
    重评：当检索结果规模增大时引入截断或重排策略
    """
    openai_embeddings = OpenAIEmbeddingsComponent()
    chat_input = ChatInput()
    rag_vector_store = AstraDBVectorStoreComponent()
    rag_vector_store.set(
        search_query=chat_input.message_response,
        embedding_model=openai_embeddings.build_embeddings,
    )

    parse_data = ParserComponent()
    parse_data.set(input_data=rag_vector_store.search_documents)
    prompt_component = PromptComponent()
    prompt_component.set(
        template=dedent("""Given the following context, answer the question.
                         Context:{context}

                         Question: {question}
                         Answer:"""),
        context=parse_data.parse_combined_text,
        question=chat_input.message_response,
    )

    openai_component = LanguageModelComponent()
    openai_component.set(input_value=prompt_component.build_prompt)

    chat_output = ChatOutput()
    chat_output.set(input_value=openai_component.text_response)

    return Graph(start=chat_input, end=chat_output)


def vector_store_rag_graph():
    """组合入库与检索的完整示例图。

    契约：返回入库图与检索图的合并结果。
    副作用：仅构图；运行时依次执行入库与检索链路。
    失败语义：任一子图失败会中断整体流程。
    关键路径：1) 执行入库 2) 执行检索问答 3) 输出结果。
    决策：通过图相加组合流程
    问题：需要最小方式连接两段独立图
    方案：使用 `Graph` 的拼接操作
    代价：子图的输入输出耦合较弱，调试需分别定位
    重评：当流程需要条件分支时改为显式连线
    """
    return ingestion_graph() + rag_graph()
