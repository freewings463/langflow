"""
模块名称：`Azure OpenAI` 文本模型组件

本模块提供基于 `Azure OpenAI` 的文本生成组件，用于将组件配置映射为 `AzureChatOpenAI` 实例。
主要功能包括：
- 提供 `Azure OpenAI` 模型参数配置
- 构建 `AzureChatOpenAI` 语言模型实例

关键组件：
- `AzureChatOpenAIComponent`

设计背景：统一 `Azure OpenAI` 模型接入入口，保持与 LangFlow 组件接口一致。
注意事项：连接失败会抛 `ValueError`，需检查 `endpoint`/`deployment`/`api_version`。
"""

from langchain_openai import AzureChatOpenAI

from lfx.base.models.model import LCModelComponent
from lfx.field_typing import LanguageModel
from lfx.field_typing.range_spec import RangeSpec
from lfx.inputs.inputs import MessageTextInput
from lfx.io import DropdownInput, IntInput, SecretStrInput, SliderInput


class AzureChatOpenAIComponent(LCModelComponent):
    """`Azure OpenAI` 文本模型组件

    契约：
    - 输入：`endpoint`、`deployment`、`api_version`、温度、最大 token 等配置
    - 输出：`AzureChatOpenAI` 语言模型实例
    - 副作用：无
    - 失败语义：连接失败时抛 `ValueError`
    """
    display_name: str = "Azure OpenAI"
    description: str = "Generate text using Azure OpenAI LLMs."
    documentation: str = "https://python.langchain.com/docs/integrations/llms/azure_openai"
    beta = False
    icon = "Azure"
    name = "AzureOpenAIModel"

    AZURE_OPENAI_API_VERSIONS = [
        "2024-06-01",
        "2024-07-01-preview",
        "2024-08-01-preview",
        "2024-09-01-preview",
        "2024-10-01-preview",
        "2023-05-15",
        "2023-12-01-preview",
        "2024-02-15-preview",
        "2024-03-01-preview",
        "2024-12-01-preview",
        "2025-01-01-preview",
        "2025-02-01-preview",
    ]

    inputs = [
        *LCModelComponent.get_base_inputs(),
        MessageTextInput(
            name="azure_endpoint",
            display_name="Azure Endpoint",
            info="Your Azure endpoint, including the resource. Example: `https://example-resource.azure.openai.com/`",
            required=True,
        ),
        MessageTextInput(name="azure_deployment", display_name="Deployment Name", required=True),
        SecretStrInput(name="api_key", display_name="Azure Chat OpenAI API Key", required=True),
        DropdownInput(
            name="api_version",
            display_name="API Version",
            options=sorted(AZURE_OPENAI_API_VERSIONS, reverse=True),
            value=next(
                (
                    version
                    for version in sorted(AZURE_OPENAI_API_VERSIONS, reverse=True)
                    if not version.endswith("-preview")
                ),
                AZURE_OPENAI_API_VERSIONS[0],
            ),
        ),
        SliderInput(
            name="temperature",
            display_name="Temperature",
            value=0.7,
            range_spec=RangeSpec(min=0, max=2, step=0.01),
            info="Controls randomness. Lower values are more deterministic, higher values are more creative.",
            advanced=True,
        ),
        IntInput(
            name="max_tokens",
            display_name="Max Tokens",
            advanced=True,
            info="The maximum number of tokens to generate. Set to 0 for unlimited tokens.",
        ),
    ]

    def build_model(self) -> LanguageModel:  # type: ignore[type-var]
        """构建 `AzureChatOpenAI` 模型实例

        关键路径（三步）：
        1) 读取组件配置字段
        2) 初始化 `AzureChatOpenAI` 客户端
        3) 返回模型实例

        异常流：初始化失败抛 `ValueError`。
        性能瓶颈：无显著性能瓶颈。
        排障入口：异常消息包含连接失败原因。
        
        契约：
        - 输入：无（使用组件字段）
        - 输出：`LanguageModel` 实例
        - 副作用：无
        - 失败语义：构建失败时抛异常
        """
        azure_endpoint = self.azure_endpoint
        azure_deployment = self.azure_deployment
        api_version = self.api_version
        api_key = self.api_key
        temperature = self.temperature
        max_tokens = self.max_tokens
        stream = self.stream

        try:
            output = AzureChatOpenAI(
                azure_endpoint=azure_endpoint,
                azure_deployment=azure_deployment,
                api_version=api_version,
                api_key=api_key,
                temperature=temperature,
                max_tokens=max_tokens or None,
                streaming=stream,
            )
        except Exception as e:
            msg = f"Could not connect to AzureOpenAI API: {e}"
            raise ValueError(msg) from e

        return output
