"""
模块名称：cloudflare

本模块提供 Cloudflare Workers AI Embeddings 组件封装。
主要功能包括：
- 构建并返回 Cloudflare 向量模型实例
- 暴露账号、模型、批量等参数供配置

关键组件：
- `CloudflareWorkersAIEmbeddingsComponent`：向量组件

设计背景：需要将 Cloudflare Workers AI 接入 Langflow
使用场景：在流程中使用 Cloudflare 向量模型
注意事项：账号与 API Token 必须有效
"""

from langchain_community.embeddings.cloudflare_workersai import CloudflareWorkersAIEmbeddings

from lfx.base.models.model import LCModelComponent
from lfx.field_typing import Embeddings
from lfx.io import BoolInput, DictInput, IntInput, MessageTextInput, Output, SecretStrInput


class CloudflareWorkersAIEmbeddingsComponent(LCModelComponent):
    """Cloudflare Workers AI Embeddings 组件。

    契约：必须提供 `account_id` 与 `api_token`，并指定 `model_name`。
    副作用：实例化外部 SDK 并可能触发认证校验。
    失败语义：初始化失败抛 `ValueError`。
    """
    display_name: str = "Cloudflare Workers AI Embeddings"
    description: str = "Generate embeddings using Cloudflare Workers AI models."
    documentation: str = "https://python.langchain.com/docs/integrations/text_embedding/cloudflare_workersai/"
    icon = "Cloudflare"
    name = "CloudflareWorkersAIEmbeddings"

    inputs = [
        MessageTextInput(
            name="account_id",
            display_name="Cloudflare account ID",
            info="Find your account ID https://developers.cloudflare.com/fundamentals/setup/find-account-and-zone-ids/#find-account-id-workers-and-pages",
            required=True,
        ),
        SecretStrInput(
            name="api_token",
            display_name="Cloudflare API token",
            info="Create an API token https://developers.cloudflare.com/fundamentals/api/get-started/create-token/",
            required=True,
        ),
        MessageTextInput(
            name="model_name",
            display_name="Model Name",
            info="List of supported models https://developers.cloudflare.com/workers-ai/models/#text-embeddings",
            required=True,
            value="@cf/baai/bge-base-en-v1.5",
        ),
        BoolInput(
            name="strip_new_lines",
            display_name="Strip New Lines",
            advanced=True,
            value=True,
        ),
        IntInput(
            name="batch_size",
            display_name="Batch Size",
            advanced=True,
            value=50,
        ),
        MessageTextInput(
            name="api_base_url",
            display_name="Cloudflare API base URL",
            advanced=True,
            value="https://api.cloudflare.com/client/v4/accounts",
        ),
        DictInput(
            name="headers",
            display_name="Headers",
            info="Additional request headers",
            is_list=True,
            advanced=True,
        ),
    ]

    outputs = [
        Output(display_name="Embeddings", name="embeddings", method="build_embeddings"),
    ]

    def build_embeddings(self) -> Embeddings:
        """构建 Cloudflare Embeddings 实例。

        契约：返回实现 `Embeddings` 协议的对象。
        副作用：创建 SDK 客户端实例。
        失败语义：初始化异常转为 `ValueError`。
        关键路径（三步）：1) 读取输入 2) 组装参数 3) 构建实例。
        决策：`headers` 允许附加自定义请求头。
        问题：部分企业网关需要额外鉴权头。
        方案：透传 `headers` 到 SDK。
        代价：调用方需自行保证头部合法。
        重评：当 SDK 提供统一的扩展认证配置时。
        """
        try:
            embeddings = CloudflareWorkersAIEmbeddings(
                account_id=self.account_id,
                api_base_url=self.api_base_url,
                api_token=self.api_token,
                batch_size=self.batch_size,
                headers=self.headers,
                model_name=self.model_name,
                strip_new_lines=self.strip_new_lines,
            )
        except Exception as e:
            msg = f"Could not connect to CloudflareWorkersAIEmbeddings API: {e!s}"
            raise ValueError(msg) from e

        return embeddings
