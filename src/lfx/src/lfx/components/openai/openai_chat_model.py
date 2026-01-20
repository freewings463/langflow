"""模块名称：OpenAI 聊天模型组件适配

本模块提供 OpenAI 聊天模型的 Langflow 组件封装，负责构建 `ChatOpenAI` 实例。
使用场景：在对话流中调用 OpenAI 模型进行文本生成或结构化输出。
主要功能包括：
- 根据模型与参数构建 `ChatOpenAI`
- 处理推理模型与普通模型的参数差异
- 支持 JSON 输出模式

关键组件：
- OpenAIModelComponent：OpenAI 聊天模型组件入口

设计背景：在 Langflow 统一模型接口上复用 OpenAI 生态
注意事项：推理模型不支持 `temperature`/`seed`，UI 需隐藏相关参数
"""

from typing import Any

from langchain_openai import ChatOpenAI
from pydantic.v1 import SecretStr

from lfx.base.models.model import LCModelComponent
from lfx.base.models.openai_constants import OPENAI_CHAT_MODEL_NAMES, OPENAI_REASONING_MODEL_NAMES
from lfx.field_typing import LanguageModel
from lfx.field_typing.range_spec import RangeSpec
from lfx.inputs.inputs import BoolInput, DictInput, DropdownInput, IntInput, SecretStrInput, SliderInput, StrInput
from lfx.log.logger import logger


class OpenAIModelComponent(LCModelComponent):
    """OpenAI 聊天模型组件，封装参数与实例化逻辑。

    契约：输入 `api_key`/`model_name`/`max_tokens` 等，输出 `LanguageModel`
    关键路径：1) 处理密钥与参数 2) 构建 `ChatOpenAI` 3) 可选 JSON 绑定
    副作用：记录调试日志；可能进行网络初始化
    异常流：构建过程中底层异常直接上抛
    排障入口：日志关键字 `Executing request` / `api_key found in model_kwargs`
    决策：推理模型禁用 `temperature`/`seed`
    问题：推理模型参数能力与普通模型不同
    方案：仅对非推理模型设置 `temperature`/`seed`，并在 UI 隐藏
    代价：部分参数在推理模型中不可配置
    重评：当推理模型支持相关参数时
    """
    display_name = "OpenAI"
    description = "Generates text using OpenAI LLMs."
    icon = "OpenAI"
    name = "OpenAIModel"

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
            options=OPENAI_CHAT_MODEL_NAMES + OPENAI_REASONING_MODEL_NAMES,
            value=OPENAI_CHAT_MODEL_NAMES[0],
            combobox=True,
            real_time_refresh=True,
        ),
        StrInput(
            name="openai_api_base",
            display_name="OpenAI API Base",
            advanced=True,
            info="The base URL of the OpenAI API. "
            "Defaults to https://api.openai.com/v1. "
            "You can change this to use other APIs like JinaChat, LocalAI and Prem.",
        ),
        SecretStrInput(
            name="api_key",
            display_name="OpenAI API Key",
            info="The OpenAI API Key to use for the OpenAI model.",
            advanced=False,
            value="OPENAI_API_KEY",
            required=True,
        ),
        SliderInput(
            name="temperature",
            display_name="Temperature",
            value=0.1,
            range_spec=RangeSpec(min=0, max=1, step=0.01),
            show=True,
        ),
        IntInput(
            name="seed",
            display_name="Seed",
            info="The seed controls the reproducibility of the job.",
            advanced=True,
            value=1,
        ),
        IntInput(
            name="max_retries",
            display_name="Max Retries",
            info="The maximum number of retries to make when generating.",
            advanced=True,
            value=5,
        ),
        IntInput(
            name="timeout",
            display_name="Timeout",
            info="The timeout for requests to OpenAI completion API.",
            advanced=True,
            value=700,
        ),
    ]

    def build_model(self) -> LanguageModel:  # type: ignore[type-var]
        """构建 OpenAI Chat 模型实例。

        关键路径（三步）：
        1) 解析密钥类型并归一化参数
        2) 处理推理模型的参数差异
        3) 初始化 `ChatOpenAI` 并按需绑定 JSON 输出

        契约：返回 `LanguageModel`；`max_tokens=0` 视为不限
        副作用：写入调试日志；可能触发网络初始化
        异常流：底层 SDK 异常直接上抛
        排障入口：日志关键字 `Final api_key_value type`
        """
        logger.debug(f"Executing request with model: {self.model_name}")
        # 注意：`api_key` 可能是 `SecretStr`，需在此统一为明文字符串。
        api_key_value = None
        if self.api_key:
            logger.debug(f"API key type: {type(self.api_key)}, value: {'***' if self.api_key else None}")
            if isinstance(self.api_key, SecretStr):
                api_key_value = self.api_key.get_secret_value()
            else:
                api_key_value = str(self.api_key)
        logger.debug(f"Final api_key_value type: {type(api_key_value)}, value: {'***' if api_key_value else None}")

        # 注意：避免 `model_kwargs` 中的 `api_key` 与显式参数冲突。
        model_kwargs = self.model_kwargs or {}
        if "api_key" in model_kwargs:
            logger.warning("api_key found in model_kwargs, removing to prevent conflicts")
            model_kwargs = dict(model_kwargs)  # 注意：复制以避免原地修改共享引用。
            del model_kwargs["api_key"]

        parameters = {
            "api_key": api_key_value,
            "model_name": self.model_name,
            "max_tokens": self.max_tokens or None,
            "model_kwargs": model_kwargs,
            "base_url": self.openai_api_base or "https://api.openai.com/v1",
            "max_retries": self.max_retries,
            "timeout": self.timeout,
        }

        # 注意：若推理模型开始支持参数，需移除该限制并恢复 UI 参数。
        unsupported_params_for_reasoning_models = ["temperature", "seed"]

        if self.model_name not in OPENAI_REASONING_MODEL_NAMES:
            parameters["temperature"] = self.temperature if self.temperature is not None else 0.1
            parameters["seed"] = self.seed
        else:
            params_str = ", ".join(unsupported_params_for_reasoning_models)
            logger.debug(f"{self.model_name} is a reasoning model, {params_str} are not configurable. Ignoring.")

        # 注意：防止 `SecretStr` 透传给底层 SDK。
        if isinstance(parameters.get("api_key"), SecretStr):
            parameters["api_key"] = parameters["api_key"].get_secret_value()
        output = ChatOpenAI(**parameters)
        if self.json_mode:
            output = output.bind(response_format={"type": "json_object"})

        return output

    def _get_exception_message(self, e: Exception):
        """从 OpenAI 异常中提取可读消息。

        契约：仅对 `BadRequestError` 提取 `body.message`，否则返回 `None`
        失败语义：OpenAI SDK 未安装时返回 `None`
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

    def update_build_config(self, build_config: dict, field_value: Any, field_name: str | None = None) -> dict:
        """根据模型类型调整可见参数。

        契约：推理模型隐藏 `temperature`/`seed`；o1 模型隐藏 `system_message`
        副作用：修改 `build_config` 中字段 `show` 状态
        """
        if field_name in {"base_url", "model_name", "api_key"} and field_value in OPENAI_REASONING_MODEL_NAMES:
            build_config["temperature"]["show"] = False
            build_config["seed"]["show"] = False
            # 注意：o1 模型当前不支持 `system_message`。
            if field_value.startswith("o1") and "system_message" in build_config:
                build_config["system_message"]["show"] = False
        if field_name in {"base_url", "model_name", "api_key"} and field_value in OPENAI_CHAT_MODEL_NAMES:
            build_config["temperature"]["show"] = True
            build_config["seed"]["show"] = True
            if "system_message" in build_config:
                build_config["system_message"]["show"] = True
        return build_config
