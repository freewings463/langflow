"""NVIDIA Embeddings 组件。

本模块封装 NVIDIA Embeddings 接口，用于生成向量表示。
主要功能包括：
- 选择 embedding 模型并动态刷新可用模型列表
- 构建嵌入模型实例

注意事项：依赖 `langchain-nvidia-ai-endpoints` 且需有效 `nvidia_api_key`。
"""

from typing import Any

from lfx.base.embeddings.model import LCEmbeddingsModel
from lfx.field_typing import Embeddings
from lfx.inputs.inputs import DropdownInput, SecretStrInput
from lfx.io import FloatInput, MessageTextInput
from lfx.schema.dotdict import dotdict


class NVIDIAEmbeddingsComponent(LCEmbeddingsModel):
    """NVIDIA Embeddings 组件封装。

    契约：输入由 `inputs` 定义；输出为 `Embeddings` 实例。
    副作用：可能触发模型列表拉取与配置刷新。
    失败语义：依赖缺失抛 `ImportError`；API 连接失败抛 `ValueError`。
    """

    display_name: str = "NVIDIA Embeddings"
    description: str = "Generate embeddings using NVIDIA models."
    icon = "NVIDIA"

    inputs = [
        DropdownInput(
            name="model",
            display_name="Model",
            options=[
                "nvidia/nv-embed-v1",
                "snowflake/arctic-embed-I",
            ],
            value="nvidia/nv-embed-v1",
            required=True,
        ),
        MessageTextInput(
            name="base_url",
            display_name="NVIDIA Base URL",
            refresh_button=True,
            value="https://integrate.api.nvidia.com/v1",
            required=True,
        ),
        SecretStrInput(
            name="nvidia_api_key",
            display_name="NVIDIA API Key",
            info="The NVIDIA API Key.",
            advanced=False,
            value="NVIDIA_API_KEY",
            required=True,
        ),
        FloatInput(
            name="temperature",
            display_name="Model Temperature",
            value=0.1,
            advanced=True,
        ),
    ]

    def update_build_config(self, build_config: dotdict, field_value: Any, field_name: str | None = None):
        """根据 `base_url` 变化刷新可选模型列表。

        契约：输入为 `build_config` 与字段值；输出更新后的 `build_config`。
        失败语义：获取模型列表失败抛 `ValueError`。
        """
        if field_name == "base_url" and field_value:
            try:
                build_model = self.build_embeddings()
                ids = [model.id for model in build_model.available_models]
                build_config["model"]["options"] = ids
                build_config["model"]["value"] = ids[0]
            except Exception as e:
                msg = f"Error getting model names: {e}"
                raise ValueError(msg) from e
        return build_config

    def build_embeddings(self) -> Embeddings:
        """构建 NVIDIA Embeddings 实例。

        契约：读取输入参数并返回 `NVIDIAEmbeddings`。
        失败语义：依赖缺失抛 `ImportError`；连接失败抛 `ValueError`。
        """
        try:
            from langchain_nvidia_ai_endpoints import NVIDIAEmbeddings
        except ImportError as e:
            msg = "Please install langchain-nvidia-ai-endpoints to use the Nvidia model."
            raise ImportError(msg) from e
        try:
            output = NVIDIAEmbeddings(
                model=self.model,
                base_url=self.base_url,
                temperature=self.temperature,
                nvidia_api_key=self.nvidia_api_key,
            )
        except Exception as e:
            msg = f"Could not connect to NVIDIA API. Error: {e}"
            raise ValueError(msg) from e
        return output
