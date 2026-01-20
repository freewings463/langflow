"""
模块名称：IBM watsonx.ai Embeddings 组件

本模块提供 watsonx.ai 向量嵌入组件，主要用于生成文本向量。主要功能包括：
- 动态拉取可用嵌入模型列表并更新 UI 选项
- 构建 `WatsonxEmbeddings` 并返回 Embeddings 实例

关键组件：
- `WatsonxEmbeddingsComponent`：嵌入模型组件

设计背景：不同区域模型列表不同，需要运行时获取。
注意事项：依赖 `ibm_watsonx_ai` 与 `langchain-ibm`，拉取失败时回退默认模型列表。
"""

from typing import Any

import requests
from ibm_watsonx_ai import APIClient, Credentials
from ibm_watsonx_ai.metanames import EmbedTextParamsMetaNames
from langchain_ibm import WatsonxEmbeddings
from pydantic.v1 import SecretStr

from lfx.base.embeddings.model import LCEmbeddingsModel
from lfx.field_typing import Embeddings
from lfx.io import BoolInput, DropdownInput, IntInput, SecretStrInput, StrInput
from lfx.log.logger import logger
from lfx.schema.dotdict import dotdict


class WatsonxEmbeddingsComponent(LCEmbeddingsModel):
    """watsonx.ai 嵌入组件。

    契约：需提供 `url`/`project_id`/`api_key` 与 `model_name`。
    失败语义：模型拉取失败时回退默认列表；SDK 调用失败时抛异常。
    副作用：可能触发网络请求与日志输出。
    """
    display_name = "IBM watsonx.ai Embeddings"
    description = "Generate embeddings using IBM watsonx.ai models."
    icon = "WatsonxAI"
    name = "WatsonxEmbeddingsComponent"

    # 注意：以下模型在所有区域均可用
    _default_models = [
        "sentence-transformers/all-minilm-l12-v2",
        "ibm/slate-125m-english-rtrvr-v2",
        "ibm/slate-30m-english-rtrvr-v2",
        "intfloat/multilingual-e5-large",
    ]

    inputs = [
        DropdownInput(
            name="url",
            display_name="watsonx API Endpoint",
            info="The base URL of the API.",
            value=None,
            options=[
                "https://us-south.ml.cloud.ibm.com",
                "https://eu-de.ml.cloud.ibm.com",
                "https://eu-gb.ml.cloud.ibm.com",
                "https://au-syd.ml.cloud.ibm.com",
                "https://jp-tok.ml.cloud.ibm.com",
                "https://ca-tor.ml.cloud.ibm.com",
            ],
            real_time_refresh=True,
        ),
        StrInput(
            name="project_id",
            display_name="watsonx project id",
            info="The project ID or deployment space ID that is associated with the foundation model.",
            required=True,
        ),
        SecretStrInput(
            name="api_key",
            display_name="Watsonx API Key",
            info="The API Key to use for the model.",
            required=True,
        ),
        DropdownInput(
            name="model_name",
            display_name="Model Name",
            options=[],
            value=None,
            dynamic=True,
            required=True,
        ),
        IntInput(
            name="truncate_input_tokens",
            display_name="Truncate Input Tokens",
            advanced=True,
            value=200,
        ),
        BoolInput(
            name="input_text",
            display_name="Include the original text in the output",
            value=True,
            advanced=True,
        ),
    ]

    @staticmethod
    def fetch_models(base_url: str) -> list[str]:
        """从 watsonx.ai API 获取可用嵌入模型列表。

        契约：请求成功返回排序后的模型 ID 列表。
        失败语义：请求/解析失败时回退到默认模型列表。
        副作用：发起网络请求。
        """
        try:
            endpoint = f"{base_url}/ml/v1/foundation_model_specs"
            params = {
                "version": "2024-09-16",
                "filters": "function_embedding,!lifecycle_withdrawn:and",
            }
            response = requests.get(endpoint, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            models = [model["model_id"] for model in data.get("resources", [])]
            return sorted(models)
        except Exception:  # noqa: BLE001
            logger.exception("Error fetching models")
            return WatsonxEmbeddingsComponent._default_models

    def update_build_config(self, build_config: dotdict, field_value: Any, field_name: str | None = None):
        """根据输入变化动态更新模型选项。

        契约：当 `url` 变化时刷新 `model_name` 选项。
        失败语义：更新失败时记录日志，不抛出。
        副作用：修改 `build_config`。
        """
        logger.debug(
            "Updating build config. Field name: %s, Field value: %s",
            field_name,
            field_value,
        )

        if field_name == "url" and field_value:
            try:
                models = self.fetch_models(base_url=build_config.url.value)
                build_config.model_name.options = models
                if build_config.model_name.value:
                    build_config.model_name.value = models[0]
                info_message = f"Updated model options: {len(models)} models found in {build_config.url.value}"
                logger.info(info_message)
            except Exception:  # noqa: BLE001
                logger.exception("Error updating model options.")

    def build_embeddings(self) -> Embeddings:
        """构建 `WatsonxEmbeddings` 实例。

        契约：返回可用于嵌入的 `Embeddings` 对象。
        失败语义：SDK 初始化失败时抛异常。
        副作用：创建 API 客户端。

        关键路径（三步）：
        1) 构建凭证并初始化 APIClient
        2) 组装嵌入参数
        3) 创建并返回 `WatsonxEmbeddings`
        """
        credentials = Credentials(
            api_key=SecretStr(self.api_key).get_secret_value(),
            url=self.url,
        )

        api_client = APIClient(credentials)

        params = {
            EmbedTextParamsMetaNames.TRUNCATE_INPUT_TOKENS: self.truncate_input_tokens,
            EmbedTextParamsMetaNames.RETURN_OPTIONS: {"input_text": self.input_text},
        }

        return WatsonxEmbeddings(
            model_id=self.model_name,
            params=params,
            watsonx_client=api_client,
            project_id=self.project_id,
        )
