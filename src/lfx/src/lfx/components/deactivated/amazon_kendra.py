"""
模块名称：Amazon Kendra 检索组件（已停用）

本模块提供基于 Amazon Kendra 的检索组件，主要用于通过 Kendra API 获取相关文档。主要功能包括：
- 构建 `AmazonKendraRetriever` 实例
- 透传索引/区域/过滤条件等配置

关键组件：
- `AmazonKendraRetrieverComponent`：Kendra 检索器组件

设计背景：历史上用于接入 Kendra 检索能力，现标记为 legacy。
注意事项：依赖 `langchain-community`；组件停用不保证长期可用性。
"""

# mypy: disable-error-code="attr-defined"
from langchain_community.retrievers import AmazonKendraRetriever

from lfx.base.vectorstores.model import check_cached_vector_store
from lfx.custom.custom_component.custom_component import CustomComponent
from lfx.io import DictInput, IntInput, StrInput


class AmazonKendraRetrieverComponent(CustomComponent):
    """Amazon Kendra 检索组件。

    契约：需提供 `index_id` 与 `region_name` 等连接参数。
    失败语义：依赖缺失抛 `ImportError`；连接失败抛 `ValueError`。
    副作用：初始化 Kendra 客户端并可能触发网络调用。
    """

    display_name: str = "Amazon Kendra Retriever"
    description: str = "Retriever that uses the Amazon Kendra API."
    name = "AmazonKendra"
    icon = "Amazon"
    legacy = True

    inputs = [
        StrInput(
            name="index_id",
            display_name="Index ID",
        ),
        StrInput(
            name="region_name",
            display_name="Region Name",
        ),
        StrInput(
            name="credentials_profile_name",
            display_name="Credentials Profile Name",
        ),
        DictInput(
            name="attribute_filter",
            display_name="Attribute Filter",
        ),
        IntInput(
            name="top_k",
            display_name="Top K",
            value=3,
        ),
        DictInput(
            name="user_context",
            display_name="User Context",
        ),
    ]

    @check_cached_vector_store
    def build_vector_store(self) -> AmazonKendraRetriever:
        """构建 Amazon Kendra 检索器。

        契约：返回 `AmazonKendraRetriever` 实例，参数与输入配置一致。
        失败语义：依赖缺失抛 `ImportError`；连接失败抛 `ValueError`。
        副作用：可能触发 Kendra API 连接验证。

        关键路径（三步）：
        1) 校验依赖并导入 Kendra 检索器
        2) 使用组件输入构造检索器
        3) 返回实例以供上层调用
        """
        try:
            from langchain_community.retrievers import AmazonKendraRetriever
        except ImportError as e:
            msg = "Could not import AmazonKendraRetriever. Please install it with `pip install langchain-community`."
            raise ImportError(msg) from e

        try:
            output = AmazonKendraRetriever(
                index_id=self.index_id,
                top_k=self.top_k,
                region_name=self.region_name,
                credentials_profile_name=self.credentials_profile_name,
                attribute_filter=self.attribute_filter,
                user_context=self.user_context,
            )
        except Exception as e:
            msg = "Could not connect to AmazonKendra API."
            raise ValueError(msg) from e

        return output
