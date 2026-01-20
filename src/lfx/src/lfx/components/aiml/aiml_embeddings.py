"""
模块名称：`AIML` 向量模型组件

本模块提供基于 `AI/ML API` 的向量模型组件，主要用于生成文本嵌入。
主要功能包括：
- 定义 `AIML` Embeddings 组件配置
- 构建 `AIMLEmbeddingsImpl` 实例

关键组件：
- `AIMLEmbeddingsComponent`

设计背景：统一 `AI/ML API` Embeddings 的配置入口。
注意事项：`API` Key 必填，模型名需匹配后端支持列表。
"""

from lfx.base.embeddings.aiml_embeddings import AIMLEmbeddingsImpl
from lfx.base.embeddings.model import LCEmbeddingsModel
from lfx.field_typing import Embeddings
from lfx.inputs.inputs import DropdownInput
from lfx.io import SecretStrInput


class AIMLEmbeddingsComponent(LCEmbeddingsModel):
    """`AI/ML API` 向量模型组件

    契约：
    - 输入：模型名与 `API` Key
    - 输出：`Embeddings` 实例
    - 副作用：无
    - 失败语义：构建失败时抛出底层异常
    """
    display_name = "AI/ML API Embeddings"
    description = "Generate embeddings using the AI/ML API."
    icon = "AIML"
    name = "AIMLEmbeddings"

    inputs = [
        DropdownInput(
            name="model_name",
            display_name="Model Name",
            options=[
                "text-embedding-3-small",
                "text-embedding-3-large",
                "text-embedding-ada-002",
            ],
            required=True,
        ),
        SecretStrInput(
            name="aiml_api_key",
            display_name="AI/ML API Key",
            value="AIML_API_KEY",
            required=True,
        ),
    ]

    def build_embeddings(self) -> Embeddings:
        """构建 `AIMLEmbeddingsImpl` 实例

        契约：
        - 输入：无（使用组件字段）
        - 输出：`Embeddings` 实例
        - 副作用：无
        - 失败语义：构建失败时抛出异常
        """
        return AIMLEmbeddingsImpl(
            api_key=self.aiml_api_key,
            model=self.model_name,
        )
