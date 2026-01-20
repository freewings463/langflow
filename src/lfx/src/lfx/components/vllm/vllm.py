"""模块名称：vLLM 文本生成模型组件

本模块封装基于 OpenAI 兼容接口的 vLLM 文本生成模型，提供参数化构建与 JSON 输出模式。
主要功能包括：构建 `ChatOpenAI`、传递可选参数、在需要时绑定 JSON 响应格式。

关键组件：
- `VllmComponent`：vLLM 文本生成组件入口

设计背景：统一 vLLM 推理服务的接入与配置方式。
注意事项：`api_base` 默认为本地 `http://localhost:8000/v1`，`seed/timeout/max_retries` 的 -1 表示禁用。
"""

from typing import Any

from langchain_openai import ChatOpenAI
from pydantic.v1 import SecretStr

from lfx.base.models.model import LCModelComponent
from lfx.field_typing import LanguageModel
from lfx.field_typing.range_spec import RangeSpec
from lfx.inputs.inputs import BoolInput, DictInput, IntInput, SecretStrInput, SliderInput, StrInput
from lfx.log.logger import logger


class VllmComponent(LCModelComponent):
    """vLLM 文本生成组件。

    契约：输入 `model_name/api_base/api_key/temperature` 等；输出 `LanguageModel`；
    副作用：无；失败语义：底层请求异常由 LangChain/OpenAI 客户端抛出。
    关键路径：1) 构建参数字典 2) 初始化 `ChatOpenAI` 3) 可选绑定 JSON 输出。
    决策：仅在参数显式设置时传入
    问题：避免将 `-1` 作为真实参数传入服务端
    方案：对 `seed/timeout/max_retries` 做条件注入
    代价：默认值由服务端决定
    重评：当需要显式默认值时改为固定传参
    """
    display_name = "vLLM"
    description = "Generates text using vLLM models via OpenAI-compatible API."
    icon = "vLLM"
    name = "vLLMModel"

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
        StrInput(
            name="model_name",
            display_name="Model Name",
            advanced=False,
            info="The name of the vLLM model to use (e.g., 'ibm-granite/granite-3.3-8b-instruct').",
            value="ibm-granite/granite-3.3-8b-instruct",
        ),
        StrInput(
            name="api_base",
            display_name="vLLM API Base",
            advanced=False,
            info="The base URL of the vLLM API server. Defaults to http://localhost:8000/v1 for local vLLM server.",
            value="http://localhost:8000/v1",
        ),
        SecretStrInput(
            name="api_key",
            display_name="API Key",
            info="The API Key to use for the vLLM model (optional for local servers).",
            advanced=False,
            value="",
            required=False,
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
            info="Controls the reproducibility of the job. Set to -1 to disable (some providers may not support).",
            advanced=True,
            value=-1,
            required=False,
        ),
        IntInput(
            name="max_retries",
            display_name="Max Retries",
            info="Max retries when generating. Set to -1 to disable (some providers may not support).",
            advanced=True,
            value=-1,
            required=False,
        ),
        IntInput(
            name="timeout",
            display_name="Timeout",
            info="Timeout for requests to vLLM completion API. Set to -1 to disable (some providers may not support).",
            advanced=True,
            value=-1,
            required=False,
        ),
    ]

    def build_model(self) -> LanguageModel:  # type: ignore[type-var]
        """构建 vLLM 模型实例。

        关键路径（三步）：
        1) 组装请求参数（含可选参数）
        2) 创建 `ChatOpenAI` 实例
        3) 若启用 `json_mode` 则绑定 JSON 响应格式

        异常流：底层网络/认证异常透传。
        排障入口：日志包含 `Executing request with vLLM model` 与模型名。
        决策：`max_tokens=0` 表示不限制
        问题：不同服务端对 0 语义不一致
        方案：当为 0 时传 `None` 让服务端决定
        代价：输出长度不可预期
        重评：当需要强制上限时显式设置
        """
        logger.debug(f"Executing request with vLLM model: {self.model_name}")
        parameters = {
            "api_key": SecretStr(self.api_key).get_secret_value() if self.api_key else None,
            "model_name": self.model_name,
            "max_tokens": self.max_tokens or None,
            "model_kwargs": self.model_kwargs or {},
            "base_url": self.api_base or "http://localhost:8000/v1",
            "temperature": self.temperature if self.temperature is not None else 0.1,
        }

        # 仅在显式设置时注入可选参数（-1 表示禁用）
        if self.seed is not None and self.seed != -1:
            parameters["seed"] = self.seed
        if self.timeout is not None and self.timeout != -1:
            parameters["timeout"] = self.timeout
        if self.max_retries is not None and self.max_retries != -1:
            parameters["max_retries"] = self.max_retries

        output = ChatOpenAI(**parameters)
        if self.json_mode:
            output = output.bind(response_format={"type": "json_object"})

        return output

    def _get_exception_message(self, e: Exception):
        """从 vLLM/OpenAI 异常中提取可读信息。

        契约：输入异常对象；输出错误消息或 `None`；副作用无；
        失败语义：缺少 `openai` 依赖时返回 `None`。
        关键路径：1) 尝试导入 `BadRequestError` 2) 提取 `e.body.message`。
        决策：仅处理 `BadRequestError`
        问题：其他异常类型无统一结构
        方案：缺省返回 `None` 交由上层处理
        代价：可能丢失更多错误细节
        重评：当错误结构统一时扩展处理范围
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

    def update_build_config(self, build_config: dict, field_value: Any, field_name: str | None = None) -> dict:  # noqa: ARG002
        """根据输入更新构建配置（vLLM 无特殊逻辑）。

        契约：输入 `build_config` 等；输出更新后的配置；副作用无；
        失败语义：无。
        关键路径：1) 原样返回配置。
        决策：不做字段隐藏/展示逻辑
        问题：vLLM 参数通用且无需模式切换
        方案：直接返回原配置
        代价：无法在 UI 层做更细粒度控制
        重评：当新增模式或依赖时加入条件逻辑
        """
        return build_config
