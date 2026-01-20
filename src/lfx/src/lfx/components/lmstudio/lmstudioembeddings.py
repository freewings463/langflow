"""
模块名称：LM Studio Embeddings 组件

本模块提供基于 LM Studio 本地服务的向量化组件，负责拉取可用模型并构建 embeddings 客户端。
主要功能包括：
- 通过 `/v1/models` 拉取模型列表用于下拉选项
- 构建 `NVIDIAEmbeddings` 实例以调用 LM Studio 兼容端点
- 提供默认 `base_url` 与可选 `api_key` 配置

关键组件：
- `LMStudioEmbeddingsComponent`：组件主体
- `get_model`：获取模型列表
- `build_embeddings`：构建 embeddings 客户端

设计背景：LM Studio 提供本地推理服务，需要在界面中动态展示模型并延迟加载依赖。
注意事项：未安装 `langchain-nvidia-ai-endpoints` 或服务不可达将抛异常。
"""

from typing import Any
from urllib.parse import urljoin

import httpx

from lfx.base.embeddings.model import LCEmbeddingsModel
from lfx.field_typing import Embeddings
from lfx.inputs.inputs import DropdownInput, SecretStrInput
from lfx.io import FloatInput, MessageTextInput


class LMStudioEmbeddingsComponent(LCEmbeddingsModel):
    """LM Studio embeddings 组件。

    契约：
    - 输入：`model` / `base_url` / `api_key` / `temperature`
    - 输出：`Embeddings` 实例供上游链路调用
    - 副作用：模型列表刷新与客户端构建期间会触发 HTTP 请求
    - 失败语义：网络/解析失败抛 `ValueError`；依赖缺失抛 `ImportError`
    """

    display_name: str = "LM Studio Embeddings"
    description: str = "Generate embeddings using LM Studio."
    icon = "LMStudio"

    async def update_build_config(self, build_config: dict, field_value: Any, field_name: str | None = None):  # noqa: ARG002
        """在模型字段变化时刷新可选模型列表。

        关键路径（三步）：
        1) 解析 `base_url` 配置并展开变量替换
        2) 缺省时回退到 `http://localhost:1234/v1`
        3) 调用 `get_model` 填充 `build_config["model"]["options"]`

        异常流：`get_model` 失败将抛 `ValueError`，由上层表单接管。
        """
        if field_name == "model":
            base_url_dict = build_config.get("base_url", {})
            base_url_load_from_db = base_url_dict.get("load_from_db", False)
            base_url_value = base_url_dict.get("value")
            if base_url_load_from_db:
                base_url_value = await self.get_variables(base_url_value, field_name)
            elif not base_url_value:
                base_url_value = "http://localhost:1234/v1"
            build_config["model"]["options"] = await self.get_model(base_url_value)

        return build_config

    @staticmethod
    async def get_model(base_url_value: str) -> list[str]:
        """从 LM Studio 端点读取模型列表。

        契约：
        - 输入：`base_url_value` 必须指向 LM Studio API 基址
        - 输出：模型 `id` 列表，缺失时返回空列表
        - 副作用：发起 `/v1/models` HTTP 请求
        - 失败语义：网络/解析异常抛 `ValueError` 并保留原始异常链
        """
        try:
            url = urljoin(base_url_value, "/v1/models")
            async with httpx.AsyncClient() as client:
                response = await client.get(url)
                response.raise_for_status()
                data = response.json()

                return [model["id"] for model in data.get("data", [])]
        except Exception as e:
            msg = "Could not retrieve models. Please, make sure the LM Studio server is running."
            raise ValueError(msg) from e

    inputs = [
        DropdownInput(
            name="model",
            display_name="Model",
            advanced=False,
            refresh_button=True,
            required=True,
        ),
        MessageTextInput(
            name="base_url",
            display_name="LM Studio Base URL",
            refresh_button=True,
            value="http://localhost:1234/v1",
            required=True,
        ),
        SecretStrInput(
            name="api_key",
            display_name="LM Studio API Key",
            advanced=True,
            value="LMSTUDIO_API_KEY",
        ),
        FloatInput(
            name="temperature",
            display_name="Model Temperature",
            value=0.1,
            advanced=True,
        ),
    ]

    def build_embeddings(self) -> Embeddings:
        """构建 embeddings 客户端并校验依赖可用性。

        契约：
        - 输入：来自组件字段的 `model` / `base_url` / `temperature` / `api_key`
        - 输出：`Embeddings` 实例（当前为 `NVIDIAEmbeddings`）
        - 副作用：构造客户端时可能触发连接校验
        - 失败语义：依赖缺失抛 `ImportError`；连接失败抛 `ValueError`
        """
        try:
            from langchain_nvidia_ai_endpoints import NVIDIAEmbeddings
        except ImportError as e:
            msg = "Please install langchain-nvidia-ai-endpoints to use LM Studio Embeddings."
            raise ImportError(msg) from e
        try:
            output = NVIDIAEmbeddings(
                model=self.model,
                base_url=self.base_url,
                temperature=self.temperature,
                nvidia_api_key=self.api_key,
            )
        except Exception as e:
            msg = f"Could not connect to LM Studio API. Error: {e}"
            raise ValueError(msg) from e
        return output
