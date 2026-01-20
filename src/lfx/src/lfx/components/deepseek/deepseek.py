"""
模块名称：deepseek

本模块提供 DeepSeek 模型组件，封装模型列表获取与 ChatOpenAI 构建逻辑。
主要功能包括：
- 功能1：动态拉取 DeepSeek 可用模型列表。
- 功能2：构建支持 JSON 模式的语言模型实例。

使用场景：在 Langflow 中接入 DeepSeek 模型进行生成或工具调用。
关键组件：
- 类 `DeepSeekModelComponent`

设计背景：复用 LangChain 的 ChatOpenAI 接口以简化模型接入。
注意事项：需要有效 API Key；模型列表拉取失败时回退默认列表。
"""

import requests
from pydantic.v1 import SecretStr
from typing_extensions import override

from lfx.base.models.model import LCModelComponent
from lfx.field_typing import LanguageModel
from lfx.field_typing.range_spec import RangeSpec
from lfx.inputs.inputs import BoolInput, DictInput, DropdownInput, IntInput, SecretStrInput, SliderInput, StrInput

DEEPSEEK_MODELS = ["deepseek-chat"]  # 注意：默认模型列表，API 不可用时作为回退。


class DeepSeekModelComponent(LCModelComponent):
    """DeepSeek 模型组件，负责模型配置与实例化。

    契约：输入包含 `api_key/api_base/model_name`；输出为 `LanguageModel`。
    关键路径：
    1) 可选拉取模型列表并更新配置；
    2) 通过 `ChatOpenAI` 构建模型；
    3) 根据 `json_mode` 绑定响应格式。
    异常流：依赖缺失或 API 调用失败会抛 `ImportError`/`RequestException`。
    排障入口：`self.status` 记录模型列表拉取错误。
    决策：
    问题：DeepSeek 与 OpenAI 接口兼容但需要自定义 base_url。
    方案：复用 `ChatOpenAI` 并注入 `base_url` 与 API Key。
    代价：依赖 langchain-openai 版本兼容性。
    重评：当 DeepSeek 提供专用 SDK 或接口变更时。
    """
    display_name = "DeepSeek"
    description = "Generate text using DeepSeek LLMs."
    icon = "DeepSeek"

    inputs = [
        *LCModelComponent.get_base_inputs(),
        IntInput(
            name="max_tokens",
            display_name="Max Tokens",
            advanced=True,
            info="Maximum number of tokens to generate. Set to 0 for unlimited.",
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
            info="DeepSeek model to use",
            options=DEEPSEEK_MODELS,
            value="deepseek-chat",
            refresh_button=True,
        ),
        StrInput(
            name="api_base",
            display_name="DeepSeek API Base",
            advanced=True,
            info="Base URL for API requests. Defaults to https://api.deepseek.com",
            value="https://api.deepseek.com",
        ),
        SecretStrInput(
            name="api_key",
            display_name="DeepSeek API Key",
            info="The DeepSeek API Key",
            advanced=False,
            required=True,
        ),
        SliderInput(
            name="temperature",
            display_name="Temperature",
            info="Controls randomness in responses",
            value=1.0,
            range_spec=RangeSpec(min=0, max=2, step=0.01),
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

    def get_models(self) -> list[str]:
        """拉取可用模型列表。

        契约：成功返回模型 ID 列表；失败回退默认列表。
        关键路径：构造 URL -> 发起请求 -> 解析 `data` 字段。
        异常流：网络错误或鉴权失败时写入状态并回退默认列表。
        决策：
        问题：模型列表可能随服务端更新，需要动态获取。
        方案：调用 `/models` 接口实时获取。
        代价：启动/配置时增加网络请求。
        重评：当模型列表固定或可缓存时。
        """
        if not self.api_key:
            return DEEPSEEK_MODELS

        url = f"{self.api_base}/models"
        headers = {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"}

        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            model_list = response.json()
            return [model["id"] for model in model_list.get("data", [])]
        except requests.RequestException as e:
            self.status = f"Error fetching models: {e}"
            return DEEPSEEK_MODELS

    @override
    def update_build_config(self, build_config: dict, field_value: str, field_name: str | None = None):
        """更新构建配置中的模型选项。

        契约：当 `api_key/api_base/model_name` 变化时刷新模型列表。
        关键路径：触发 `get_models` -> 更新 `build_config` 选项。
        决策：
        问题：模型列表依赖 API Key 与 Base URL。
        方案：在相关字段变更时重新拉取。
        代价：可能多次触发网络请求。
        重评：当引入缓存或异步刷新机制时。
        """
        if field_name in {"api_key", "api_base", "model_name"}:
            models = self.get_models()
            build_config["model_name"]["options"] = models
        return build_config

    def build_model(self) -> LanguageModel:
        """构建 DeepSeek 语言模型实例。

        契约：返回 `LanguageModel`；缺失依赖时抛 `ImportError`。
        关键路径：导入 `ChatOpenAI` -> 组装参数 -> 可选 JSON 模式绑定。
        异常流：依赖缺失抛异常。
        决策：
        问题：需要统一模型实例化并支持 JSON 输出模式。
        方案：使用 `ChatOpenAI` 并在 `json_mode` 时绑定响应格式。
        代价：受限于 `ChatOpenAI` 的参数与兼容性。
        重评：当 DeepSeek 提供原生 client 或参数变化时。
        """
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as e:
            msg = "langchain-openai not installed. Please install with `pip install langchain-openai`"
            raise ImportError(msg) from e

        api_key = SecretStr(self.api_key).get_secret_value() if self.api_key else None
        output = ChatOpenAI(
            model=self.model_name,
            temperature=self.temperature if self.temperature is not None else 0.1,
            max_tokens=self.max_tokens or None,
            model_kwargs=self.model_kwargs or {},
            base_url=self.api_base,
            api_key=api_key,
            streaming=self.stream if hasattr(self, "stream") else False,
            seed=self.seed,
        )

        if self.json_mode:
            output = output.bind(response_format={"type": "json_object"})

        return output

    def _get_exception_message(self, e: Exception):
        """从 DeepSeek/OpenAI 异常中提取可读错误信息。

        契约：若识别为 `BadRequestError` 则返回 message，否则返回 `None`。
        关键路径：尝试导入 `BadRequestError` -> 类型判断 -> 提取 `body.message`。
        决策：
        问题：默认异常信息可能不够清晰。
        方案：针对 `BadRequestError` 提取服务端 message。
        代价：依赖 openai 包，缺失时回退为 `None`。
        重评：当错误结构统一或提供标准化错误码时。
        """
        try:
            from openai import BadRequestError

            if isinstance(e, BadRequestError):
                message = e.body.get("message")
                if message:
                    return message
        except ImportError:
            pass
        return None
