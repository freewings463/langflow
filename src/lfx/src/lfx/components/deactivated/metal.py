"""
模块名称：Metal 检索组件（已停用）

本模块提供基于 Metal API 的检索组件，主要用于旧流程中接入 Metal 检索服务。主要功能包括：
- 构建 Metal 客户端并封装为 `MetalRetriever`

关键组件：
- `MetalRetrieverComponent`：Metal 检索器组件

设计背景：历史上用于接入 Metal 检索能力，现标记为 legacy。
注意事项：依赖 `metal-sdk` 与 `langchain-community`。
"""

# mypy: disable-error-code="attr-defined"
from langchain_community.retrievers import MetalRetriever

from lfx.base.vectorstores.model import check_cached_vector_store
from lfx.custom.custom_component.custom_component import CustomComponent
from lfx.io import DictInput, SecretStrInput, StrInput


class MetalRetrieverComponent(CustomComponent):
    """Metal 检索组件。

    契约：需提供 `api_key`/`client_id`/`index_id`。
    失败语义：依赖缺失抛 `ImportError`；连接失败抛 `ValueError`。
    副作用：初始化 Metal 客户端并可能触发网络调用。
    """
    display_name: str = "Metal Retriever"
    description: str = "Retriever that uses the Metal API."
    name = "MetalRetriever"
    legacy = True

    inputs = [
        SecretStrInput(
            name="api_key",
            display_name="Metal Retriever API Key",
            required=True,
        ),
        SecretStrInput(
            name="client_id",
            display_name="Client ID",
            required=True,
        ),
        StrInput(
            name="index_id",
            display_name="Index ID",
            required=True,
        ),
        DictInput(
            name="params",
            display_name="Parameters",
            required=False,
        ),
    ]

    @check_cached_vector_store
    def build_vector_store(self) -> MetalRetriever:
        """构建 Metal 检索器。

        契约：返回 `MetalRetriever` 实例，使用输入参数初始化。
        失败语义：依赖缺失抛 `ImportError`；连接失败抛 `ValueError`。
        副作用：可能触发 Metal API 连接。

        关键路径（三步）：
        1) 校验依赖并导入 Metal SDK
        2) 创建 Metal 客户端
        3) 返回 `MetalRetriever`
        """
        try:
            from langchain_community.retrievers import MetalRetriever
            from metal_sdk.metal import Metal
        except ImportError as e:
            msg = "Could not import Metal. Please install it with `pip install metal-sdk langchain-community`."
            raise ImportError(msg) from e

        try:
            metal = Metal(api_key=self.api_key, client_id=self.client_id, index_id=self.index_id)
        except Exception as e:
            msg = "Could not connect to Metal API."
            raise ValueError(msg) from e

        return MetalRetriever(client=metal, params=self.params or {})
