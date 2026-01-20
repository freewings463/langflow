"""
模块名称：chunk_docling_document

本模块提供 DoclingDocument 的分块组件封装，支持 Hybrid/Hierarchical 两种分块器。
主要功能包括：
- 根据所选分块器对文档进行分块
- 将分块结果转为 `DataFrame`

关键组件：
- `ChunkDoclingDocumentComponent`：文档分块组件

设计背景：将结构化文档切分为可检索/可索引的片段
使用场景：文档切分、索引前处理
注意事项：HybridChunker 依赖 tokenizer 提供方
"""

import json

import tiktoken
from docling_core.transforms.chunker import BaseChunker, DocMeta
from docling_core.transforms.chunker.hierarchical_chunker import HierarchicalChunker

from lfx.base.data.docling_utils import extract_docling_documents
from lfx.custom import Component
from lfx.io import DropdownInput, HandleInput, IntInput, MessageTextInput, Output, StrInput
from lfx.schema import Data, DataFrame


class ChunkDoclingDocumentComponent(Component):
    """DoclingDocument 分块组件。

    契约：输入 `Data`/`DataFrame`，输出包含分块文本的 `DataFrame`。
    副作用：可能更新 `status` 提示警告。
    失败语义：依赖缺失或分块失败会抛异常。
    决策：同一组件内支持多种分块策略。
    问题：单一分块策略难以覆盖不同文档类型需求。
    方案：暴露 Hybrid/Hierarchical 两种策略供选择。
    代价：配置项增多且依赖组合更复杂。
    重评：当上游统一分块策略或依赖稳定时。
    """
    display_name: str = "Chunk DoclingDocument"
    description: str = "Use the DocumentDocument chunkers to split the document into chunks."
    documentation = "https://docling-project.github.io/docling/concepts/chunking/"
    icon = "Docling"
    name = "ChunkDoclingDocument"

    inputs = [
        HandleInput(
            name="data_inputs",
            display_name="Data or DataFrame",
            info="The data with documents to split in chunks.",
            input_types=["Data", "DataFrame"],
            required=True,
        ),
        DropdownInput(
            name="chunker",
            display_name="Chunker",
            options=["HybridChunker", "HierarchicalChunker"],
            info=("Which chunker to use."),
            value="HybridChunker",
            real_time_refresh=True,
        ),
        DropdownInput(
            name="provider",
            display_name="Provider",
            options=["Hugging Face", "OpenAI"],
            info=("Which tokenizer provider."),
            value="Hugging Face",
            show=True,
            real_time_refresh=True,
            advanced=True,
            dynamic=True,
        ),
        StrInput(
            name="hf_model_name",
            display_name="HF model name",
            info=(
                "Model name of the tokenizer to use with the HybridChunker when Hugging Face is chosen as a tokenizer."
            ),
            value="sentence-transformers/all-MiniLM-L6-v2",
            show=True,
            advanced=True,
            dynamic=True,
        ),
        StrInput(
            name="openai_model_name",
            display_name="OpenAI model name",
            info=("Model name of the tokenizer to use with the HybridChunker when OpenAI is chosen as a tokenizer."),
            value="gpt-4o",
            show=False,
            advanced=True,
            dynamic=True,
        ),
        IntInput(
            name="max_tokens",
            display_name="Maximum tokens",
            info=("Maximum number of tokens for the HybridChunker."),
            show=True,
            required=False,
            advanced=True,
            dynamic=True,
        ),
        MessageTextInput(
            name="doc_key",
            display_name="Doc Key",
            info="The key to use for the DoclingDocument column.",
            value="doc",
            advanced=True,
        ),
    ]

    outputs = [
        Output(display_name="DataFrame", name="dataframe", method="chunk_documents"),
    ]

    def update_build_config(self, build_config: dict, field_value: str, field_name: str | None = None) -> dict:
        """根据选择的分块器/提供方刷新可见配置项。

        契约：仅更新 `build_config` 展示状态。
        副作用：修改输入项的 `show` 标志。
        失败语义：无显式异常。
        关键路径（三步）：1) 判断变更字段 2) 更新相关显示项 3) 返回配置。
        性能瓶颈：无显著性能开销。
        决策：HybridChunker 才暴露 tokenizer 相关选项。
        问题：不相关选项会误导用户配置。
        方案：按分块器/提供方动态显示。
        代价：配置逻辑变复杂，需要维护映射关系。
        重评：当 UI 支持更复杂的条件渲染时。
        """
        if field_name == "chunker":
            provider_type = build_config["provider"]["value"]
            is_hf = provider_type == "Hugging Face"
            is_openai = provider_type == "OpenAI"
            if field_value == "HybridChunker":
                build_config["provider"]["show"] = True
                build_config["hf_model_name"]["show"] = is_hf
                build_config["openai_model_name"]["show"] = is_openai
                build_config["max_tokens"]["show"] = True
            else:
                build_config["provider"]["show"] = False
                build_config["hf_model_name"]["show"] = False
                build_config["openai_model_name"]["show"] = False
                build_config["max_tokens"]["show"] = False
        elif field_name == "provider" and build_config["chunker"]["value"] == "HybridChunker":
            if field_value == "Hugging Face":
                build_config["hf_model_name"]["show"] = True
                build_config["openai_model_name"]["show"] = False
            elif field_value == "OpenAI":
                build_config["hf_model_name"]["show"] = False
                build_config["openai_model_name"]["show"] = True

        return build_config

    def _docs_to_data(self, docs) -> list[Data]:
        """将 Docling 文档转换为 `Data` 列表。

        契约：入参为 Docling 文档列表。
        失败语义：无显式异常。
        """
        return [Data(text=doc.page_content, data=doc.metadata) for doc in docs]

    def chunk_documents(self) -> DataFrame:
        """对文档进行分块并输出 DataFrame。

        契约：`data_inputs` 中需包含 `doc_key` 指定列。
        副作用：可能记录 `status` 警告。
        失败语义：依赖缺失抛 `ImportError`，分块失败抛 `TypeError`。
        关键路径（三步）：
        1) 提取 Docling 文档并选择分块器。
        2) 构建 tokenizer 与 chunker。
        3) 生成分块并组装 `DataFrame`。
        异常流：缺少 docling-core/transformers 依赖，或分块内部错误。
        性能瓶颈：tokenizer 编码与 chunker 切分。
        排障入口：异常信息包含缺失依赖或分块错误原因。
        决策：在运行时根据配置选择分块器与 tokenizer。
        问题：不同文档类型对分块策略和 tokenizer 依赖不同。
        方案：提供 Hybrid/Hierarchical 选择并动态构建依赖。
        代价：运行时导入与配置路径更复杂。
        重评：当团队统一分块策略或依赖收敛时。
        """
        documents, warning = extract_docling_documents(self.data_inputs, self.doc_key)
        if warning:
            self.status = warning

        chunker: BaseChunker
        if self.chunker == "HybridChunker":
            try:
                from docling_core.transforms.chunker.hybrid_chunker import HybridChunker
            except ImportError as e:
                msg = (
                    "HybridChunker is not installed. Please install it with `uv pip install docling-core[chunking] "
                    "or `uv pip install transformers`"
                )
                raise ImportError(msg) from e
            max_tokens: int | None = self.max_tokens if self.max_tokens else None
            if self.provider == "Hugging Face":
                try:
                    from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer
                except ImportError as e:
                    msg = (
                        "HuggingFaceTokenizer is not installed."
                        " Please install it with `uv pip install docling-core[chunking]`"
                    )
                    raise ImportError(msg) from e
                tokenizer = HuggingFaceTokenizer.from_pretrained(
                    model_name=self.hf_model_name,
                    max_tokens=max_tokens,
                )
            elif self.provider == "OpenAI":
                try:
                    from docling_core.transforms.chunker.tokenizer.openai import OpenAITokenizer
                except ImportError as e:
                    msg = (
                        "OpenAITokenizer is not installed."
                        " Please install it with `uv pip install docling-core[chunking]`"
                        " or `uv pip install transformers`"
                    )
                    raise ImportError(msg) from e
                if max_tokens is None:
                    # 注意：OpenAI tokenizer 需显式上下文长度。
                    max_tokens = 128 * 1024
                tokenizer = OpenAITokenizer(
                    tokenizer=tiktoken.encoding_for_model(self.openai_model_name), max_tokens=max_tokens
                )
            chunker = HybridChunker(
                tokenizer=tokenizer,
            )
        elif self.chunker == "HierarchicalChunker":
            chunker = HierarchicalChunker()

        results: list[Data] = []
        try:
            for doc in documents:
                for chunk in chunker.chunk(dl_doc=doc):
                    enriched_text = chunker.contextualize(chunk=chunk)
                    meta = DocMeta.model_validate(chunk.meta)

                    results.append(
                        Data(
                            data={
                                "text": enriched_text,
                                "document_id": f"{doc.origin.binary_hash}",
                                "doc_items": json.dumps([item.self_ref for item in meta.doc_items]),
                            }
                        )
                    )

        except Exception as e:
            msg = f"Error splitting text: {e}"
            raise TypeError(msg) from e

        return DataFrame(results)
