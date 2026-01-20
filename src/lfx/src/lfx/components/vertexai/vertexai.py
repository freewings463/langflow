"""模块名称：Vertex AI 聊天模型组件适配

本模块提供 Google Vertex AI 聊天模型的 Langflow 组件封装。
使用场景：在对话流中调用 Vertex AI 模型生成文本。
主要功能包括：
- 构建 `ChatVertexAI` 实例并注入项目/区域/凭证
- 支持从服务账号文件或环境变量加载凭证

关键组件：
- ChatVertexAIComponent：Vertex AI 聊天模型组件入口

设计背景：统一 Langflow 模型接口，兼容 GCP 原生鉴权流程
注意事项：传入 `credentials` 时会显式调用 `aiplatform.init`
"""

from typing import cast

from lfx.base.models.model import LCModelComponent
from lfx.field_typing import LanguageModel
from lfx.inputs.inputs import MessageTextInput
from lfx.io import BoolInput, FileInput, FloatInput, IntInput, StrInput


class ChatVertexAIComponent(LCModelComponent):
    """Vertex AI 聊天模型组件，封装鉴权与实例化。

    契约：输入 `credentials`/`project`/`location` 等，输出 `LanguageModel`
    关键路径：1) 检查依赖 2) 初始化凭证/项目 3) 构建 `ChatVertexAI`
    副作用：可能调用 `aiplatform.init` 设置全局状态
    异常流：缺少依赖抛 `ImportError`
    排障入口：`ImportError` 提示安装 `langchain-google-vertexai`
    决策：当传入凭证文件时主动初始化 `aiplatform`
    问题：`ChatVertexAI` 有时会跳过手动凭证初始化
    方案：在构建前显式 `aiplatform.init`
    代价：引入全局初始化副作用
    重评：当 SDK 保证显式凭证生效或支持传参隔离时
    """
    display_name = "Vertex AI"
    description = "Generate text using Vertex AI LLMs."
    icon = "VertexAI"
    name = "VertexAiModel"

    inputs = [
        *LCModelComponent.get_base_inputs(),
        FileInput(
            name="credentials",
            display_name="Credentials",
            info="JSON credentials file. Leave empty to fallback to environment variables",
            file_types=["json"],
        ),
        MessageTextInput(name="model_name", display_name="Model Name", value="gemini-1.5-pro"),
        StrInput(name="project", display_name="Project", info="The project ID.", advanced=True),
        StrInput(name="location", display_name="Location", value="us-central1", advanced=True),
        IntInput(name="max_output_tokens", display_name="Max Output Tokens", advanced=True),
        IntInput(name="max_retries", display_name="Max Retries", value=1, advanced=True),
        FloatInput(name="temperature", value=0.0, display_name="Temperature"),
        IntInput(name="top_k", display_name="Top K", advanced=True),
        FloatInput(name="top_p", display_name="Top P", value=0.95, advanced=True),
        BoolInput(name="verbose", display_name="Verbose", value=False, advanced=True),
    ]

    def build_model(self) -> LanguageModel:
        """构建 Vertex AI Chat 模型实例。

        关键路径（三步）：
        1) 检查依赖并加载 SDK
        2) 处理凭证与项目/区域
        3) 初始化 `ChatVertexAI`

        契约：返回 `LanguageModel`
        副作用：可能调用 `aiplatform.init` 设置全局配置
        异常流：缺少依赖抛 `ImportError`
        """
        try:
            from langchain_google_vertexai import ChatVertexAI
        except ImportError as e:
            msg = "Please install the langchain-google-vertexai package to use the VertexAIEmbeddings component."
            raise ImportError(msg) from e
        location = self.location or None
        if self.credentials:
            from google.cloud import aiplatform
            from google.oauth2 import service_account

            credentials = service_account.Credentials.from_service_account_file(self.credentials)
            project = self.project or credentials.project_id
            # 注意：显式初始化可避免 SDK 忽略手动凭证。
            aiplatform.init(
                project=project,
                location=location,
                credentials=credentials,
            )
        else:
            project = self.project or None
            credentials = None

        return cast(
            "LanguageModel",
            ChatVertexAI(
                credentials=credentials,
                location=location,
                project=project,
                max_output_tokens=self.max_output_tokens or None,
                max_retries=self.max_retries,
                model_name=self.model_name,
                temperature=self.temperature,
                top_k=self.top_k or None,
                top_p=self.top_p,
                verbose=self.verbose,
            ),
        )
