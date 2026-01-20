"""
模块名称：`Google Generative AI` 向量组件

本模块提供 `GoogleGenerativeAIEmbeddingsComponent`，用于生成文本向量并支持降维输出。
主要功能包括：
- 构建 `GoogleGenerativeAIEmbeddings` 实例
- 批量/单条嵌入并校验维度
- 统一异常为 `GoogleGenerativeAIError`

关键组件：`GoogleGenerativeAIEmbeddingsComponent`
设计背景：为 `Google` 向量服务提供可控的批量嵌入能力
注意事项：输出维度范围为 `[1, 768]`；依赖 `langchain_google_genai`
"""

# TODO：待 `google` 包发布类型后移除忽略
from google.ai.generativelanguage_v1beta.types import BatchEmbedContentsRequest
from langchain_core.embeddings import Embeddings
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_google_genai._common import GoogleGenerativeAIError

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output, SecretStrInput

MIN_DIMENSION_ERROR = "Output dimensionality must be at least 1"
MAX_DIMENSION_ERROR = (
    "Output dimensionality cannot exceed 768. Google's embedding models only support dimensions up to 768."
)
MAX_DIMENSION = 768
MIN_DIMENSION = 1


class GoogleGenerativeAIEmbeddingsComponent(Component):
    """`Google Generative AI` 向量组件。
    契约：输入为 `API Key` 与模型名；输出为 `Embeddings` 实例。
    关键路径：校验 `API Key` → 构建嵌入类 → 返回实例。
    决策：在组件内覆盖嵌入实现。问题：需要批量与降维控制；方案：子类覆盖；代价：维护成本；重评：当上游提供稳定能力时。
    """

    display_name = "Google Generative AI Embeddings"
    description = (
        "Connect to Google's generative AI embeddings service using the GoogleGenerativeAIEmbeddings class, "
        "found in the langchain-google-genai package."
    )
    documentation: str = "https://python.langchain.com/v0.2/docs/integrations/text_embedding/google_generative_ai/"
    icon = "GoogleGenerativeAI"
    name = "Google Generative AI Embeddings"

    inputs = [
        SecretStrInput(name="api_key", display_name="Google Generative AI API Key", required=True),
        MessageTextInput(name="model_name", display_name="Model Name", value="models/text-embedding-004"),
    ]

    outputs = [
        Output(display_name="Embeddings", name="embeddings", method="build_embeddings"),
    ]

    def build_embeddings(self) -> Embeddings:
        """构建嵌入模型实例。
        契约：返回 `Embeddings`；缺少 `API Key` 时抛 `ValueError`。
        关键路径：校验 `API Key` → 定义子类 → 返回实例。
        决策：内联定义子类以覆盖行为。问题：避免全局副作用；方案：局部子类；代价：每次构建重复定义；重评：当需要全局复用时。
        """
        if not self.api_key:
            msg = "API Key is required"
            raise ValueError(msg)

        class HotaGoogleGenerativeAIEmbeddings(GoogleGenerativeAIEmbeddings):
            def __init__(self, *args, **kwargs) -> None:
                """初始化嵌入实例。
                契约：调用父类构造并保持参数透传。
                关键路径：直接调用父类 `__init__`。
                决策：保持与上游初始化行为一致。问题：避免参数丢失；方案：透传；代价：无法在此处注入校验；重评：当需要额外初始化逻辑时。
                """
                super(GoogleGenerativeAIEmbeddings, self).__init__(*args, **kwargs)

            def embed_documents(
                self,
                texts: list[str],
                *,
                batch_size: int = 100,
                task_type: str | None = None,
                titles: list[str] | None = None,
                output_dimensionality: int | None = 768,
            ) -> list[list[float]]:
                """批量生成嵌入向量。
                契约：返回与输入文本等长的向量列表；维度不合法时抛 `ValueError`。
                关键路径：校验维度 → 分批构建请求 → 调用 `batch_embed_contents` → 聚合结果。
                决策：默认批量大小 `100`。问题：服务端限制；方案：分批调用；代价：多次网络请求；重评：当服务端上限变化时。
                """
                if output_dimensionality is not None and output_dimensionality < MIN_DIMENSION:
                    raise ValueError(MIN_DIMENSION_ERROR)
                if output_dimensionality is not None and output_dimensionality > MAX_DIMENSION:
                    error_msg = MAX_DIMENSION_ERROR.format(output_dimensionality)
                    raise ValueError(error_msg)

                embeddings: list[list[float]] = []
                batch_start_index = 0
                for batch in GoogleGenerativeAIEmbeddings._prepare_batches(texts, batch_size):
                    if titles:
                        titles_batch = titles[batch_start_index : batch_start_index + len(batch)]
                        batch_start_index += len(batch)
                    else:
                        titles_batch = [None] * len(batch)  # type: ignore[list-item]

                    requests = [
                        self._prepare_request(
                            text=text,
                            task_type=task_type,
                            title=title,
                            output_dimensionality=output_dimensionality,
                        )
                        for text, title in zip(batch, titles_batch, strict=True)
                    ]

                    try:
                        result = self.client.batch_embed_contents(
                            BatchEmbedContentsRequest(requests=requests, model=self.model)
                        )
                    except Exception as e:
                        msg = f"Error embedding content: {e}"
                        raise GoogleGenerativeAIError(msg) from e
                    embeddings.extend([list(e.values) for e in result.embeddings])
                return embeddings

            def embed_query(
                self,
                text: str,
                task_type: str | None = None,
                title: str | None = None,
                output_dimensionality: int | None = 768,
            ) -> list[float]:
                """生成单条文本嵌入。
                契约：返回单条向量；维度不合法时抛 `ValueError`。
                关键路径：校验维度 → 设定 `task_type` → 调用 `embed_documents`。
                决策：默认 `task_type=RETRIEVAL_QUERY`。问题：保证查询语义一致；方案：默认值；代价：不适配其他场景；重评：当需要动态任务类型时。
                """
                if output_dimensionality is not None and output_dimensionality < MIN_DIMENSION:
                    raise ValueError(MIN_DIMENSION_ERROR)
                if output_dimensionality is not None and output_dimensionality > MAX_DIMENSION:
                    error_msg = MAX_DIMENSION_ERROR.format(output_dimensionality)
                    raise ValueError(error_msg)

                task_type = task_type or "RETRIEVAL_QUERY"
                return self.embed_documents(
                    [text],
                    task_type=task_type,
                    titles=[title] if title else None,
                    output_dimensionality=output_dimensionality,
                )[0]

        return HotaGoogleGenerativeAIEmbeddings(model=self.model_name, google_api_key=self.api_key)
