"""
模块名称：Vectara RAG 组件

模块目的：封装 Vectara RAG 检索+摘要链路并输出回答消息。
使用场景：在流程中根据查询返回带摘要的最终答案。
主要功能包括：
- 配置 RAG 检索参数（重排、摘要、语言）
- 构建 Vectara RAG 对象并执行查询
- 将回答封装为 `Message`

关键组件：
- `VectaraRagComponent`：RAG 组件入口

设计背景：利用 Vectara 原生 RAG 能力降低链路编排成本。
注意：依赖 `langchain-community`，缺失会抛 `ImportError`。
"""

from lfx.custom.custom_component.component import Component
from lfx.field_typing.range_spec import RangeSpec
from lfx.io import DropdownInput, FloatInput, IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.message import Message


class VectaraRagComponent(Component):
    """Vectara RAG 组件。

    契约：输入 Vectara 账号信息与查询文本，输出 `Message`。
    关键路径：`generate_response` 构建 RAG 配置并调用 `invoke`。

    决策：通过 Vectara 原生 `as_rag` 能力实现端到端 RAG
    问题：需要减少检索与摘要的手工编排
    方案：直接使用 Vectara 提供的 RAG API
    代价：对 Vectara 专用配置耦合较高
    重评：当需要自定义检索/摘要策略或切换供应商时
    """
    display_name = "Vectara RAG"
    description = "Vectara's full end to end RAG"
    documentation = "https://docs.vectara.com/docs"
    icon = "Vectara"
    name = "VectaraRAG"
    SUMMARIZER_PROMPTS = [
        "vectara-summary-ext-24-05-sml",
        "vectara-summary-ext-24-05-med-omni",
        "vectara-summary-ext-24-05-large",
        "vectara-summary-ext-24-05-med",
        "vectara-summary-ext-v1.3.0",
    ]

    RERANKER_TYPES = ["mmr", "rerank_multilingual_v1", "none"]

    RESPONSE_LANGUAGES = [
        "auto",
        "eng",
        "spa",
        "fra",
        "zho",
        "deu",
        "hin",
        "ara",
        "por",
        "ita",
        "jpn",
        "kor",
        "rus",
        "tur",
        "fas",
        "vie",
        "tha",
        "heb",
        "nld",
        "ind",
        "pol",
        "ukr",
        "ron",
        "swe",
        "ces",
        "ell",
        "ben",
        "msa",
        "urd",
    ]

    field_order = ["vectara_customer_id", "vectara_corpus_id", "vectara_api_key", "search_query", "reranker"]

    inputs = [
        StrInput(name="vectara_customer_id", display_name="Vectara Customer ID", required=True),
        StrInput(name="vectara_corpus_id", display_name="Vectara Corpus ID", required=True),
        SecretStrInput(name="vectara_api_key", display_name="Vectara API Key", required=True),
        MessageTextInput(
            name="search_query",
            display_name="Search Query",
            info="The query to receive an answer on.",
            tool_mode=True,
        ),
        FloatInput(
            name="lexical_interpolation",
            display_name="Hybrid Search Factor",
            range_spec=RangeSpec(min=0.005, max=0.1, step=0.005),
            value=0.005,
            advanced=True,
            info="How much to weigh lexical scores compared to the embedding score. "
            "0 means lexical search is not used at all, and 1 means only lexical search is used.",
        ),
        MessageTextInput(
            name="filter",
            display_name="Metadata Filters",
            value="",
            advanced=True,
            info="The filter string to narrow the search to according to metadata attributes.",
        ),
        DropdownInput(
            name="reranker",
            display_name="Reranker Type",
            options=RERANKER_TYPES,
            value=RERANKER_TYPES[0],
            info="How to rerank the retrieved search results.",
        ),
        IntInput(
            name="reranker_k",
            display_name="Number of Results to Rerank",
            value=50,
            range_spec=RangeSpec(min=1, max=100, step=1),
            advanced=True,
        ),
        FloatInput(
            name="diversity_bias",
            display_name="Diversity Bias",
            value=0.2,
            range_spec=RangeSpec(min=0, max=1, step=0.01),
            advanced=True,
            info="Ranges from 0 to 1, with higher values indicating greater diversity (only applies to MMR reranker).",
        ),
        IntInput(
            name="max_results",
            display_name="Max Results to Summarize",
            value=7,
            range_spec=RangeSpec(min=1, max=100, step=1),
            advanced=True,
            info="The maximum number of search results to be available to the prompt.",
        ),
        DropdownInput(
            name="response_lang",
            display_name="Response Language",
            options=RESPONSE_LANGUAGES,
            value="eng",
            advanced=True,
            info="Use the ISO 639-1 or 639-3 language code or auto to automatically detect the language.",
        ),
        DropdownInput(
            name="prompt",
            display_name="Prompt Name",
            options=SUMMARIZER_PROMPTS,
            value=SUMMARIZER_PROMPTS[0],
            advanced=True,
            info="Only vectara-summary-ext-24-05-sml is for Growth customers; "
            "all other prompts are for Scale customers only.",
        ),
    ]

    outputs = [
        Output(name="answer", display_name="Answer", method="generate_response"),
    ]

    def generate_response(
        self,
    ) -> Message:
        """执行 RAG 查询并返回回答消息。

        契约：`search_query` 非空时返回回答文本；为空可能返回空结果。
        副作用：调用外部 Vectara 服务（网络 I/O）。

        关键路径（三步）：
        1) 载入 Vectara 依赖并构建客户端
        2) 组装 rerank/summary/query 配置并创建 RAG
        3) 调用 `invoke` 获取回答并封装 `Message`

        注意：依赖缺失抛 `ImportError`；鉴权失败在调用阶段抛异常。
        性能：检索与摘要耗时受 `max_results` 与模型负载影响。
        排障：关注上游异常堆栈与 Vectara API 返回错误信息。
        """
        text_output = ""

        try:
            from langchain_community.vectorstores import Vectara
            from langchain_community.vectorstores.vectara import RerankConfig, SummaryConfig, VectaraQueryConfig
        except ImportError as e:
            msg = "Could not import Vectara. Please install it with `pip install langchain-community`."
            raise ImportError(msg) from e

        vectara = Vectara(self.vectara_customer_id, self.vectara_corpus_id, self.vectara_api_key)
        rerank_config = RerankConfig(self.reranker, self.reranker_k, self.diversity_bias)
        summary_config = SummaryConfig(
            is_enabled=True, max_results=self.max_results, response_lang=self.response_lang, prompt_name=self.prompt
        )
        config = VectaraQueryConfig(
            lambda_val=self.lexical_interpolation,
            filter=self.filter,
            summary_config=summary_config,
            rerank_config=rerank_config,
        )
        rag = vectara.as_rag(config)
        response = rag.invoke(self.search_query, config={"callbacks": self.get_langchain_callbacks()})

        text_output = response["answer"]

        return Message(text=text_output)
