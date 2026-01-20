"""模块名称：假向量嵌入组件

本模块提供 LangChain `FakeEmbeddings` 的组件化封装，用于测试或占位联通。
主要功能包括：按指定维度构造可预测的嵌入对象。

关键组件：
- `FakeEmbeddingsComponent`：生成假向量的嵌入模型适配层

设计背景：在无真实模型依赖时验证流程连通性。
注意事项：输出不具备语义，仅用于测试与占位。
"""

from langchain_community.embeddings import FakeEmbeddings

from lfx.base.embeddings.model import LCEmbeddingsModel
from lfx.field_typing import Embeddings
from lfx.io import IntInput


class FakeEmbeddingsComponent(LCEmbeddingsModel):
    """假向量嵌入组件。

    契约：输入 `dimensions`；输出 `Embeddings` 实例；副作用无；
    失败语义：`dimensions` 为空时回退到 5。
    关键路径：1) 读取维度 2) 构造 `FakeEmbeddings` 3) 返回模型。
    决策：默认维度为 5
    问题：测试场景需最小可用配置
    方案：在空值时回退常量维度
    代价：维度过小导致下游验证不足
    重评：当测试覆盖需要高维度时提高默认值
    """
    display_name = "Fake Embeddings"
    description = "Generate fake embeddings, useful for initial testing and connecting components."
    icon = "LangChain"
    name = "LangChainFakeEmbeddings"

    inputs = [
        IntInput(
            name="dimensions",
            display_name="Dimensions",
            info="The number of dimensions the resulting output embeddings should have.",
            value=5,
        ),
    ]

    def build_embeddings(self) -> Embeddings:
        """构建假嵌入模型。

        契约：输入 `dimensions`；输出 `FakeEmbeddings`；副作用无；
        失败语义：无。
        关键路径：1) 读取维度 2) 初始化模型。
        决策：优先使用用户配置维度
        问题：需要覆盖不同维度的测试
        方案：`self.dimensions or 5` 回退
        代价：维度不一致可能导致缓存命中率下降
        重评：当引入维度统一策略时移除回退
        """
        return FakeEmbeddings(
            size=self.dimensions or 5,
        )
