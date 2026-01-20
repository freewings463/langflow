"""模块名称：语义切分组件

本模块封装 LangChain Experimental 的 `SemanticChunker`，基于嵌入相似度进行语义分块。
主要功能包括：校验输入 `Data`、构建分块参数、输出分块结果。

关键组件：
- `SemanticTextSplitterComponent`：语义切分器的组件化入口

设计背景：相较固定字符切分更贴近语义边界。
注意事项：必须提供 `embeddings`，实验特性可能随版本变化。
"""

from langchain.docstore.document import Document
from langchain_experimental.text_splitter import SemanticChunker

from lfx.base.textsplitters.model import LCTextSplitterComponent
from lfx.io import (
    DropdownInput,
    FloatInput,
    HandleInput,
    IntInput,
    MessageTextInput,
    Output,
)
from lfx.schema.data import Data


class SemanticTextSplitterComponent(LCTextSplitterComponent):
    """语义切分组件。

    契约：输入 `data_inputs/embeddings/breakpoint_*`；输出 `list[Data]`；
    副作用：更新 `self.status`；失败语义：缺失 `embeddings` 或输入为空会抛 `ValueError`。
    关键路径：1) 校验输入并转换 `Document` 2) 构建 `SemanticChunker` 3) 输出分块结果。
    决策：使用 `SemanticChunker` 实验实现
    问题：需要语义感知分块能力
    方案：依赖 `langchain_experimental` 提供的切分器
    代价：API 可能不稳定
    重评：当稳定实现进入主包时迁移
    """

    display_name: str = "Semantic Text Splitter"
    name: str = "SemanticTextSplitter"
    description: str = "Split text into semantically meaningful chunks using semantic similarity."
    documentation = "https://python.langchain.com/docs/how_to/semantic-chunker/"
    beta = True  # 该组件来自 `langchain_experimental`，保持 beta 标记
    icon = "LangChain"

    inputs = [
        HandleInput(
            name="data_inputs",
            display_name="Data Inputs",
            info="List of Data objects containing text and metadata to split.",
            input_types=["Data"],
            is_list=True,
            required=True,
        ),
        HandleInput(
            name="embeddings",
            display_name="Embeddings",
            info="Embeddings model to use for semantic similarity. Required.",
            input_types=["Embeddings"],
            is_list=False,
            required=True,
        ),
        DropdownInput(
            name="breakpoint_threshold_type",
            display_name="Breakpoint Threshold Type",
            info=(
                "Method to determine breakpoints. Options: 'percentile', "
                "'standard_deviation', 'interquartile'. Defaults to 'percentile'."
            ),
            value="percentile",
            options=["percentile", "standard_deviation", "interquartile"],
        ),
        FloatInput(
            name="breakpoint_threshold_amount",
            display_name="Breakpoint Threshold Amount",
            info="Numerical amount for the breakpoint threshold.",
            value=0.5,
        ),
        IntInput(
            name="number_of_chunks",
            display_name="Number of Chunks",
            info="Number of chunks to split the text into.",
            value=5,
        ),
        MessageTextInput(
            name="sentence_split_regex",
            display_name="Sentence Split Regex",
            info="Regular expression to split sentences. Optional.",
            value="",
            advanced=True,
        ),
        IntInput(
            name="buffer_size",
            display_name="Buffer Size",
            info="Size of the buffer.",
            value=0,
            advanced=True,
        ),
    ]

    outputs = [
        Output(display_name="Chunks", name="chunks", method="split_text"),
    ]

    def _docs_to_data(self, docs: list[Document]) -> list[Data]:
        """将 `Document` 列表转换为 `Data` 列表。

        契约：输入 `Document` 列表；输出 `Data` 列表；副作用无；
        失败语义：输入为空则返回空列表。
        关键路径：1) 遍历文档 2) 拷贝 `page_content/metadata`。
        决策：仅保留 `page_content` 与 `metadata`
        问题：`Data` 结构需轻量化
        方案：丢弃 `Document` 其他字段
        代价：可能丢失自定义属性
        重评：当 `Data` 扩展字段时补充映射
        """
        return [Data(text=doc.page_content, data=doc.metadata) for doc in docs]

    def split_text(self) -> list[Data]:
        """执行语义分块并返回 `Data` 列表。

        关键路径（三步）：
        1) 校验 `embeddings` 与输入数据类型
        2) 构造 `SemanticChunker` 并生成 `Document`
        3) 转换为 `Data` 并写入状态

        异常流：输入类型错误抛 `TypeError`；其他异常包装为 `RuntimeError`。
        性能瓶颈：嵌入计算与语义分段。
        排障入口：异常信息包含 `SemanticTextSplitter`。
        决策：统一将异常包装为 `RuntimeError`
        问题：需要对上层提供一致错误语义
        方案：捕获并重抛带上下文信息的异常
        代价：丢失原始异常类型
        重评：当上层支持细粒度异常处理时保留原类型
        """
        try:
            embeddings = getattr(self, "embeddings", None)
            if embeddings is None:
                error_msg = "An embeddings model is required for SemanticTextSplitter."
                raise ValueError(error_msg)

            if not self.data_inputs:
                error_msg = "Data inputs cannot be empty."
                raise ValueError(error_msg)

            documents = []
            for _input in self.data_inputs:
                if isinstance(_input, Data):
                    documents.append(_input.to_lc_document())
                else:
                    error_msg = f"Invalid data input type: {_input}"
                    raise TypeError(error_msg)

            if not documents:
                error_msg = "No valid Data objects found in data_inputs."
                raise ValueError(error_msg)

            texts = [doc.page_content for doc in documents]
            metadatas = [doc.metadata for doc in documents]

            splitter_params = {
                "embeddings": embeddings,
                "breakpoint_threshold_type": self.breakpoint_threshold_type or "percentile",
                "breakpoint_threshold_amount": self.breakpoint_threshold_amount,
                "number_of_chunks": self.number_of_chunks,
                "buffer_size": self.buffer_size,
            }

            if self.sentence_split_regex:
                splitter_params["sentence_split_regex"] = self.sentence_split_regex

            splitter = SemanticChunker(**splitter_params)
            docs = splitter.create_documents(texts, metadatas=metadatas)

            data = self._docs_to_data(docs)
            self.status = data

        except Exception as e:
            error_msg = f"An error occurred during semantic splitting: {e}"
            raise RuntimeError(error_msg) from e

        else:
            return data
