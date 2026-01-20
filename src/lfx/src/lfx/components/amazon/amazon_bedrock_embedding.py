"""
模块名称：Amazon Bedrock 向量嵌入组件

本模块提供基于 Bedrock Embeddings 的组件封装，用于生成文本向量。主要功能包括：
- 组装 Bedrock Embeddings 客户端与认证参数
- 暴露标准化的 `Embeddings` 输出

关键组件：
- `AmazonBedrockEmbeddingsComponent`

设计背景：不同向量嵌入模型需统一接入 LFX 组件体系。
使用场景：在向量化或检索流程中生成文本向量。
注意事项：依赖 `langchain_aws` 与 `boto3`。
"""

from lfx.base.models.aws_constants import AWS_EMBEDDING_MODEL_IDS, AWS_REGIONS
from lfx.base.models.model import LCModelComponent
from lfx.field_typing import Embeddings
from lfx.inputs.inputs import SecretStrInput
from lfx.io import DropdownInput, MessageTextInput, Output


class AmazonBedrockEmbeddingsComponent(LCModelComponent):
    """Bedrock Embeddings 组件

    契约：输入模型 ID、AWS 凭证与区域；输出 `Embeddings`；
    副作用：创建 boto3 客户端；失败语义：依赖缺失抛 `ImportError`。
    关键路径：1) 创建 boto3 Session 2) 构建 bedrock-runtime 客户端 3) 返回 `BedrockEmbeddings`。
    决策：优先使用显式凭证，其次使用 profile，最后使用默认配置。
    问题：用户凭证来源多样且需兼容。
    方案：按优先级选择 Session 构建方式。
    代价：错误配置可能导致隐式使用默认凭证。
    重评：当统一凭证管理或强制配置来源时。
    """
    display_name: str = "Amazon Bedrock Embeddings"
    description: str = "Generate embeddings using Amazon Bedrock models."
    icon = "Amazon"
    name = "AmazonBedrockEmbeddings"

    inputs = [
        DropdownInput(
            name="model_id",
            display_name="Model Id",
            options=AWS_EMBEDDING_MODEL_IDS,
            value="amazon.titan-embed-text-v1",
        ),
        SecretStrInput(
            name="aws_access_key_id",
            display_name="AWS Access Key ID",
            info="The access key for your AWS account."
            "Usually set in Python code as the environment variable 'AWS_ACCESS_KEY_ID'.",
            value="AWS_ACCESS_KEY_ID",
            required=True,
        ),
        SecretStrInput(
            name="aws_secret_access_key",
            display_name="AWS Secret Access Key",
            info="The secret key for your AWS account. "
            "Usually set in Python code as the environment variable 'AWS_SECRET_ACCESS_KEY'.",
            value="AWS_SECRET_ACCESS_KEY",
            required=True,
        ),
        SecretStrInput(
            name="aws_session_token",
            display_name="AWS Session Token",
            advanced=False,
            info="The session key for your AWS account. "
            "Only needed for temporary credentials. "
            "Usually set in Python code as the environment variable 'AWS_SESSION_TOKEN'.",
            value="AWS_SESSION_TOKEN",
        ),
        SecretStrInput(
            name="credentials_profile_name",
            display_name="Credentials Profile Name",
            advanced=True,
            info="The name of the profile to use from your "
            "~/.aws/credentials file. "
            "If not provided, the default profile will be used.",
            value="AWS_CREDENTIALS_PROFILE_NAME",
        ),
        DropdownInput(
            name="region_name",
            display_name="Region Name",
            value="us-east-1",
            options=AWS_REGIONS,
            info="The AWS region where your Bedrock resources are located.",
        ),
        MessageTextInput(
            name="endpoint_url",
            display_name="Endpoint URL",
            advanced=True,
            info="The URL of the AWS Bedrock endpoint to use.",
        ),
    ]

    outputs = [
        Output(display_name="Embeddings", name="embeddings", method="build_embeddings"),
    ]

    def build_embeddings(self) -> Embeddings:
        """构建 Bedrock Embeddings 实例

        契约：读取组件输入并返回 `BedrockEmbeddings`；副作用：创建客户端连接；
        失败语义：依赖缺失抛 `ImportError`，boto3 客户端创建失败时抛异常。
        关键路径（三步）：1) 解析依赖 2) 生成 Session 与客户端 3) 返回嵌入对象。
        决策：将 `endpoint_url` 与 `region_name` 直接透传给客户端。
        问题：部分环境需要自定义终端或区域。
        方案：通过输入字段显式配置。
        代价：错误配置会导致请求失败。
        重评：当统一端点配置或自动发现区域时。
        """
        try:
            from langchain_aws import BedrockEmbeddings
        except ImportError as e:
            msg = "langchain_aws is not installed. Please install it with `pip install langchain_aws`."
            raise ImportError(msg) from e
        try:
            import boto3
        except ImportError as e:
            msg = "boto3 is not installed. Please install it with `pip install boto3`."
            raise ImportError(msg) from e
        if self.aws_access_key_id or self.aws_secret_access_key:
            session = boto3.Session(
                aws_access_key_id=self.aws_access_key_id,
                aws_secret_access_key=self.aws_secret_access_key,
                aws_session_token=self.aws_session_token,
            )
        elif self.credentials_profile_name:
            session = boto3.Session(profile_name=self.credentials_profile_name)
        else:
            session = boto3.Session()

        client_params = {}
        if self.endpoint_url:
            client_params["endpoint_url"] = self.endpoint_url
        if self.region_name:
            client_params["region_name"] = self.region_name

        boto3_client = session.client("bedrock-runtime", **client_params)
        return BedrockEmbeddings(
            credentials_profile_name=self.credentials_profile_name,
            client=boto3_client,
            model_id=self.model_id,
            endpoint_url=self.endpoint_url,
            region_name=self.region_name,
        )
