"""
模块名称：LM Studio 文本模型组件

本模块提供基于 LM Studio 本地服务的对话模型组件，用于构建 LangChain 的 `ChatOpenAI` 客户端。
主要功能包括：
- 通过 `/v1/models` 动态获取模型列表
- 组装模型参数（温度、seed、max_tokens 等）并构建客户端
- 提供异常信息解析辅助（`BadRequestError`）

关键组件：
- `LMStudioModelComponent`
- `get_model`
- `build_model`
- `_get_exception_message`

设计背景：LM Studio 暴露 OpenAI 兼容接口，需要在 UI 中配置并生成可调用模型对象。
注意事项：默认基址为 `http://localhost:1234/v1`，不可达时仅记录日志。
"""

from typing import Any
from urllib.parse import urljoin

import httpx
from langchain_openai import ChatOpenAI

from lfx.base.models.model import LCModelComponent
from lfx.field_typing import LanguageModel
from lfx.field_typing.range_spec import RangeSpec
from lfx.inputs.inputs import DictInput, DropdownInput, FloatInput, IntInput, SecretStrInput, StrInput


class LMStudioModelComponent(LCModelComponent):
    """LM Studio 文本模型组件。

    契约：
    - 输入：`model_name` / `base_url` / `api_key` / `temperature` / `max_tokens` / `seed`
    - 输出：`LanguageModel`（`ChatOpenAI` 实例）
    - 副作用：模型列表刷新会发起 HTTP 请求；构建阶段不主动访问网络
    - 失败语义：模型列表获取失败时返回原配置并记录日志
    """

    display_name = "LM Studio"
    description = "Generate text using LM Studio Local LLMs."
    icon = "LMStudio"
    name = "LMStudioModel"

    async def update_build_config(self, build_config: dict, field_value: Any, field_name: str | None = None):  # noqa: ARG002
        """在模型名称变化时刷新可选模型列表。

        关键路径（三步）：
        1) 从 `base_url` 字段解析实际地址（必要时做变量替换）
        2) 先探测 `/v1/models` 可达性（超时 2s）
        3) 成功后填充 `build_config["model_name"]["options"]`

        异常流：探测失败会记录日志并返回原配置，不抛异常。
        """
        if field_name == "model_name":
            base_url_dict = build_config.get("base_url", {})
            base_url_load_from_db = base_url_dict.get("load_from_db", False)
            base_url_value = base_url_dict.get("value")
            if base_url_load_from_db:
                base_url_value = await self.get_variables(base_url_value, field_name)
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(urljoin(base_url_value, "/v1/models"), timeout=2.0)
                    response.raise_for_status()
            except httpx.HTTPError:
                msg = "Could not access the default LM Studio URL. Please, specify the 'Base URL' field."
                self.log(msg)
                return build_config
            build_config["model_name"]["options"] = await self.get_model(base_url_value)

        return build_config

    @staticmethod
    async def get_model(base_url_value: str) -> list[str]:
        """从 LM Studio 端点拉取模型列表。

        契约：
        - 输入：`base_url_value` 为 LM Studio API 基址
        - 输出：模型 `id` 列表；无数据时返回空列表
        - 副作用：发起 `/v1/models` HTTP 请求
        - 失败语义：网络/解析异常抛 `ValueError`
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
        *LCModelComponent.get_base_inputs(),
        IntInput(
            name="max_tokens",
            display_name="Max Tokens",
            advanced=True,
            info="The maximum number of tokens to generate. Set to 0 for unlimited tokens.",
            range_spec=RangeSpec(min=0, max=128000),
        ),
        DictInput(name="model_kwargs", display_name="Model Kwargs", advanced=True),
        DropdownInput(
            name="model_name",
            display_name="Model Name",
            advanced=False,
            refresh_button=True,
        ),
        StrInput(
            name="base_url",
            display_name="Base URL",
            advanced=False,
            info="Endpoint of the LM Studio API. Defaults to 'http://localhost:1234/v1' if not specified.",
            value="http://localhost:1234/v1",
        ),
        SecretStrInput(
            name="api_key",
            display_name="LM Studio API Key",
            info="The LM Studio API Key to use for LM Studio.",
            advanced=True,
            value="LMSTUDIO_API_KEY",
        ),
        FloatInput(
            name="temperature",
            display_name="Temperature",
            value=0.1,
            advanced=True,
        ),
        IntInput(
            name="seed",
            display_name="Seed",
            info="The seed controls the reproducibility of the job.",
            advanced=True,
            value=1,
        ),
    ]

    def build_model(self) -> LanguageModel:  # type: ignore[type-var]
        """组装 `ChatOpenAI` 客户端参数并返回实例。

        契约：
        - 输入：组件字段中的模型名、温度、seed、max_tokens、base_url 等
        - 输出：可调用的 `LanguageModel` 实例
        - 副作用：仅构造对象，不触发模型请求
        - 失败语义：参数不合法或依赖异常时抛出构造期异常
        """
        lmstudio_api_key = self.api_key
        temperature = self.temperature
        model_name: str = self.model_name
        max_tokens = self.max_tokens
        model_kwargs = self.model_kwargs or {}
        base_url = self.base_url or "http://localhost:1234/v1"
        seed = self.seed

        return ChatOpenAI(
            max_tokens=max_tokens or None,
            model_kwargs=model_kwargs,
            model=model_name,
            base_url=base_url,
            api_key=lmstudio_api_key,
            temperature=temperature if temperature is not None else 0.1,
            seed=seed,
        )

    def _get_exception_message(self, e: Exception):
        """从 LM Studio 异常中提取用户可读信息。

        契约：
        - 输入：捕获到的异常对象
        - 输出：`BadRequestError` 的 message 字段；其他情况返回 `None`
        - 副作用：无
        - 失败语义：`openai` 未安装或异常类型不匹配时返回 `None`
        """
        try:
            from openai import BadRequestError
        except ImportError:
            return None
        if isinstance(e, BadRequestError):
            message = e.body.get("message")
            if message:
                return message
        return None
