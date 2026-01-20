"""模块名称：Novita LLM 组件适配

本模块提供 Novita AI 的 OpenAI 兼容模型接入能力，供 Langflow 组件系统调用。
使用场景：在对话流中调用 Novita 模型进行文本生成或结构化输出。
主要功能包括：
- 拉取可用模型列表并刷新 UI 选项
- 构建 `ChatOpenAI` 实例并指向 Novita `base_url`
- 支持可选 JSON 输出模式

关键组件：
- NovitaModelComponent：Novita 模型组件入口

设计背景：复用 Langflow 现有 OpenAI 生态，降低新模型接入成本
注意事项：模型列表拉取失败会回退到 `MODEL_NAMES`
"""

import requests
from langchain_openai import ChatOpenAI
from pydantic.v1 import SecretStr
from typing_extensions import override

from lfx.base.models.model import LCModelComponent
from lfx.base.models.novita_constants import MODEL_NAMES
from lfx.field_typing import LanguageModel
from lfx.field_typing.range_spec import RangeSpec
from lfx.inputs.inputs import (
    BoolInput,
    DictInput,
    DropdownInput,
    HandleInput,
    IntInput,
    SecretStrInput,
    SliderInput,
)


class NovitaModelComponent(LCModelComponent):
    """Novita 模型组件，提供 OpenAI 兼容接口的文本生成。

    契约：输入 `api_key`/`model_name`/`max_tokens` 等，输出 `LanguageModel`
    关键路径：1) 刷新模型列表 2) 构建 `ChatOpenAI` 3) 可选 JSON 绑定
    副作用：模型列表刷新会发起网络请求
    异常流：列表请求失败回退 `MODEL_NAMES`；连接失败抛 `ValueError`
    排障入口：`status` 字段包含 `Error fetching models`
    决策：以 OpenAI 兼容 `base_url` 接入 Novita
    问题：需要与 Langflow 现有 OpenAI 生态复用
    方案：使用 `ChatOpenAI` 并指定 `base_url`
    代价：受限于 OpenAI 兼容层能力
    重评：当 Novita 原生 SDK 能提供更完整能力时
    """
    display_name = "Novita AI"
    description = "Generates text using Novita AI LLMs (OpenAI compatible)."
    icon = "Novita"
    name = "NovitaModel"

    inputs = [
        *LCModelComponent.get_base_inputs(),
        IntInput(
            name="max_tokens",
            display_name="Max Tokens",
            advanced=True,
            info="The maximum number of tokens to generate. Set to 0 for unlimited tokens.",
            range_spec=RangeSpec(min=0, max=128000),
        ),
        DictInput(
            name="model_kwargs",
            display_name="Model Kwargs",
            advanced=True,
            info="Additional keyword arguments to pass to the model.",
        ),
        BoolInput(
            name="json_mode",
            display_name="JSON Mode",
            advanced=True,
            info="If True, it will output JSON regardless of passing a schema.",
        ),
        DropdownInput(
            name="model_name",
            display_name="Model Name",
            advanced=False,
            options=MODEL_NAMES,
            value=MODEL_NAMES[0],
            refresh_button=True,
        ),
        SecretStrInput(
            name="api_key",
            display_name="Novita API Key",
            info="The Novita API Key to use for Novita AI models.",
            advanced=False,
            value="NOVITA_API_KEY",
            real_time_refresh=True,
        ),
        SliderInput(name="temperature", display_name="Temperature", value=0.1, range_spec=RangeSpec(min=0, max=1)),
        IntInput(
            name="seed",
            display_name="Seed",
            info="The seed controls the reproducibility of the job.",
            advanced=True,
            value=1,
        ),
        HandleInput(
            name="output_parser",
            display_name="Output Parser",
            info="The parser to use to parse the output of the model",
            advanced=True,
            input_types=["OutputParser"],
        ),
    ]

    def get_models(self) -> list[str]:
        """拉取 Novita 模型列表。

        关键路径（三步）：
        1) 组装请求地址与 headers
        2) 调用 `/models` 并校验状态码
        3) 解析 `data` 并返回模型 id 列表

        异常流：网络/接口异常时设置 `status` 并回退 `MODEL_NAMES`
        性能瓶颈：受网络延迟影响，默认超时 10 秒
        排障入口：组件状态 `status` 字段
        """
        base_url = "https://api.novita.ai/v3/openai"
        url = f"{base_url}/models"

        headers = {"Content-Type": "application/json"}

        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            model_list = response.json()
            return [model["id"] for model in model_list.get("data", [])]
        except requests.RequestException as e:
            self.status = f"Error fetching models: {e}"
            return MODEL_NAMES

    @override
    def update_build_config(self, build_config: dict, field_value: str, field_name: str | None = None):
        """按字段变化刷新模型下拉选项。

        契约：仅在 `api_key` 或 `model_name` 变更时刷新 `model_name.options`
        副作用：调用 `get_models` 可能触发网络请求
        """
        if field_name in {"api_key", "model_name"}:
            models = self.get_models()
            build_config["model_name"]["options"] = models
        return build_config

    def build_model(self) -> LanguageModel:  # type: ignore[type-var]
        """构建 Novita ChatOpenAI 模型实例。

        关键路径（三步）：
        1) 汇总输入参数并补齐默认值
        2) 初始化 `ChatOpenAI` 并指定 Novita `base_url`
        3) `json_mode` 开启时绑定 `response_format`

        契约：返回 `LanguageModel`；`max_tokens=0` 视为不限
        副作用：可能进行网络初始化；使用 `SecretStr` 处理密钥
        异常流：连接失败抛 `ValueError("Could not connect to Novita API.")`
        性能瓶颈：首次初始化受网络影响
        排障入口：异常消息 `Could not connect to Novita API.`
        决策：`json_mode` 时强制绑定 JSON 输出
        问题：无 schema 时仍需稳定 JSON 结构
        方案：调用 `bind` 设置 `response_format={"type": "json_object"}`
        代价：可能与模型默认输出不一致
        重评：当上游提供标准化结构化输出控制时
        """
        api_key = self.api_key
        temperature = self.temperature
        model_name: str = self.model_name
        max_tokens = self.max_tokens
        model_kwargs = self.model_kwargs or {}
        json_mode = self.json_mode
        seed = self.seed

        try:
            output = ChatOpenAI(
                model=model_name,
                api_key=(SecretStr(api_key).get_secret_value() if api_key else None),
                max_tokens=max_tokens or None,
                temperature=temperature,
                model_kwargs=model_kwargs,
                streaming=self.stream,
                seed=seed,
                base_url="https://api.novita.ai/v3/openai",
            )
        except Exception as e:
            msg = "Could not connect to Novita API."
            raise ValueError(msg) from e

        if json_mode:
            output = output.bind(response_format={"type": "json_object"})

        return output
