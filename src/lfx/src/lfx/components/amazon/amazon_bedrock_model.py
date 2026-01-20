"""
模块名称：Amazon Bedrock 旧版模型组件

本模块提供基于旧版 ChatBedrock API 的 LLM 组件实现，用于兼容旧模型。主要功能包括：
- 组装 ChatBedrock 初始化参数
- 处理 AWS 认证、区域与模型附加参数

关键组件：
- `AmazonBedrockComponent`

设计背景：旧版 API 仍被部分模型或环境使用，需要保留兼容路径。
使用场景：在迁移到 Converse 之前继续使用旧版 Bedrock 模型。
注意事项：该组件已标记为 legacy，建议优先使用 Converse 组件。
"""

from lfx.base.models.aws_constants import AWS_REGIONS, AWS_MODEL_IDs
from lfx.base.models.model import LCModelComponent
from lfx.field_typing import LanguageModel
from lfx.inputs.inputs import MessageTextInput, SecretStrInput
from lfx.io import DictInput, DropdownInput


class AmazonBedrockComponent(LCModelComponent):
    """Bedrock 旧版模型组件

    契约：输入模型 ID、AWS 凭证、区域与 `model_kwargs`；输出 `LanguageModel`；
    副作用：创建 boto3 客户端；失败语义：依赖缺失抛 `ImportError`，连接失败抛 `ValueError`。
    关键路径：1) 创建 Session 2) 构建 bedrock-runtime 客户端 3) 初始化 `ChatBedrock`。
    决策：保留 legacy 组件并提供 replacement 提示。
    问题：旧流程仍依赖 ChatBedrock API。
    方案：标记为 legacy 且指向替代组件。
    代价：维护双栈实现，增加长期成本。
    重评：当旧 API 完全下线或无存量用户时。
    """
    display_name: str = "Amazon Bedrock"
    description: str = (
        "Generate text using Amazon Bedrock LLMs with the legacy ChatBedrock API. "
        "This component is deprecated. Please use Amazon Bedrock Converse instead "
        "for better compatibility, newer features, and improved conversation handling."
    )
    icon = "Amazon"
    name = "AmazonBedrockModel"
    legacy = True
    replacement = "amazon.AmazonBedrockConverseModel"

    inputs = [
        *LCModelComponent.get_base_inputs(),
        DropdownInput(
            name="model_id",
            display_name="Model ID",
            options=AWS_MODEL_IDs,
            value="anthropic.claude-3-haiku-20240307-v1:0",
            info="List of available model IDs to choose from.",
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
            load_from_db=False,
        ),
        SecretStrInput(
            name="credentials_profile_name",
            display_name="Credentials Profile Name",
            advanced=True,
            info="The name of the profile to use from your "
            "~/.aws/credentials file. "
            "If not provided, the default profile will be used.",
            load_from_db=False,
        ),
        DropdownInput(
            name="region_name",
            display_name="Region Name",
            value="us-east-1",
            options=AWS_REGIONS,
            info="The AWS region where your Bedrock resources are located.",
        ),
        DictInput(
            name="model_kwargs",
            display_name="Model Kwargs",
            advanced=True,
            is_list=True,
            info="Additional keyword arguments to pass to the model.",
        ),
        MessageTextInput(
            name="endpoint_url",
            display_name="Endpoint URL",
            advanced=True,
            info="The URL of the Bedrock endpoint to use.",
        ),
    ]

    def build_model(self) -> LanguageModel:  # type: ignore[type-var]
        """构建 ChatBedrock 语言模型

        契约：读取组件输入并返回 `ChatBedrock`；副作用：创建客户端连接；
        失败语义：依赖缺失抛 `ImportError`，会话/连接失败抛 `ValueError`。
        关键路径（三步）：1) 解析凭证并创建 Session 2) 构建客户端 3) 初始化模型实例。
        决策：对 Session 创建失败抛 `ValueError` 而非透传。
        问题：底层异常信息对用户不友好且缺少上下文。
        方案：统一错误提示。
        代价：丢失部分异常细节。
        重评：当错误处理统一改为结构化异常时。
        """
        try:
            from langchain_aws import ChatBedrock
        except ImportError as e:
            msg = "langchain_aws is not installed. Please install it with `pip install langchain_aws`."
            raise ImportError(msg) from e
        try:
            import boto3
        except ImportError as e:
            msg = "boto3 is not installed. Please install it with `pip install boto3`."
            raise ImportError(msg) from e
        if self.aws_access_key_id or self.aws_secret_access_key:
            try:
                session = boto3.Session(
                    aws_access_key_id=self.aws_access_key_id,
                    aws_secret_access_key=self.aws_secret_access_key,
                    aws_session_token=self.aws_session_token,
                )
            except Exception as e:
                msg = "Could not create a boto3 session."
                raise ValueError(msg) from e
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
        try:
            output = ChatBedrock(
                client=boto3_client,
                model_id=self.model_id,
                region_name=self.region_name,
                model_kwargs=self.model_kwargs,
                endpoint_url=self.endpoint_url,
                streaming=self.stream,
            )
        except Exception as e:
            msg = "Could not connect to AmazonBedrock API."
            raise ValueError(msg) from e
        return output
