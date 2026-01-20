"""模块名称：xAI 模型组件

本模块封装 xAI（Grok）模型的构建与模型列表获取逻辑，基于 OpenAI 兼容接口调用。
主要功能包括：拉取可用模型、构建 `ChatOpenAI`、支持 JSON 模式输出。

关键组件：
- `XAIModelComponent`：xAI 模型组件入口

设计背景：统一 xAI 服务的模型选择与请求参数管理。
注意事项：`api_key` 必填；模型列表获取依赖网络请求。
"""

import requests
from langchain_openai import ChatOpenAI
from pydantic.v1 import SecretStr
from typing_extensions import override

from lfx.base.models.model import LCModelComponent
from lfx.field_typing import LanguageModel
from lfx.field_typing.range_spec import RangeSpec
from lfx.inputs.inputs import (
    BoolInput,
    DictInput,
    DropdownInput,
    IntInput,
    MessageTextInput,
    SecretStrInput,
    SliderInput,
)

XAI_DEFAULT_MODELS = ["grok-2-latest"]


class XAIModelComponent(LCModelComponent):
    """xAI 模型组件。

    契约：输入 `api_key/model_name/base_url` 等；输出 `LanguageModel`；
    副作用：可能发起模型列表请求；失败语义：网络错误回退到默认模型列表。
    关键路径：1) 获取模型列表（可选）2) 构建 `ChatOpenAI` 3) 绑定 JSON 模式。
    决策：模型列表获取失败时回退默认值
    问题：避免模型选择 UI 因网络失败而不可用
    方案：异常时返回 `XAI_DEFAULT_MODELS`
    代价：可能无法展示最新模型
    重评：当引入缓存/重试策略时优化
    """
    display_name = "xAI"
    description = "Generates text using xAI models like Grok."
    icon = "xAI"
    name = "xAIModel"

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
            options=XAI_DEFAULT_MODELS,
            value=XAI_DEFAULT_MODELS[0],
            refresh_button=True,
            combobox=True,
            info="The xAI model to use",
        ),
        MessageTextInput(
            name="base_url",
            display_name="xAI API Base",
            advanced=True,
            info="The base URL of the xAI API. Defaults to https://api.x.ai/v1",
            value="https://api.x.ai/v1",
        ),
        SecretStrInput(
            name="api_key",
            display_name="xAI API Key",
            info="The xAI API Key to use for the model.",
            advanced=False,
            value="XAI_API_KEY",
            required=True,
        ),
        SliderInput(
            name="temperature",
            display_name="Temperature",
            value=0.1,
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
        """从 xAI API 拉取可用模型列表。

        契约：输入无；输出模型名列表；副作用：发起网络请求；
        失败语义：请求异常时返回默认模型列表并写入 `self.status`。
        关键路径：1) 构建请求 2) 解析 `models/aliases` 3) 排序返回。
        决策：合并 `id` 与 `aliases`
        问题：模型可能有别名，需统一展示
        方案：使用 set 去重后排序
        代价：返回顺序与服务端不一致
        重评：当需要保留服务端排序时改为原序返回
        """
        if not self.api_key:
            return XAI_DEFAULT_MODELS

        base_url = self.base_url or "https://api.x.ai/v1"
        url = f"{base_url}/language-models"
        headers = {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"}

        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()

            # 提取模型 ID 与别名
            models = set()
            for model in data.get("models", []):
                models.add(model["id"])
                models.update(model.get("aliases", []))

            return sorted(models) if models else XAI_DEFAULT_MODELS
        except requests.RequestException as e:
            self.status = f"Error fetching models: {e}"
            return XAI_DEFAULT_MODELS

    @override
    def update_build_config(self, build_config: dict, field_value: str, field_name: str | None = None):
        """在关键字段变化时刷新模型列表。

        契约：输入 `build_config` 与字段变更信息；输出更新后的配置；
        副作用：可能发起模型列表请求；失败语义：请求失败时仍返回默认列表。
        关键路径：1) 判断触发字段 2) 获取模型列表 3) 更新选项。
        决策：仅在 `api_key/base_url/model_name` 变化时刷新
        问题：避免频繁网络请求
        方案：限制触发字段
        代价：其他字段变化不会刷新
        重评：当需要更实时刷新时扩展触发条件
        """
        if field_name in {"api_key", "base_url", "model_name"}:
            models = self.get_models()
            build_config["model_name"]["options"] = models
        return build_config

    def build_model(self) -> LanguageModel:
        """构建 xAI 模型实例。

        关键路径（三步）：
        1) 读取配置并规范化参数
        2) 创建 `ChatOpenAI` 实例
        3) 可选绑定 JSON 响应格式

        异常流：认证/网络异常由底层客户端抛出。
        排障入口：`self.status` 中的模型拉取错误信息。
        决策：`max_tokens=0` 作为不限制
        问题：不同服务端对 0 语义不一致
        方案：当为 0 时传 `None`
        代价：输出长度不可预期
        重评：当需要强制上限时显式设置
        """
        api_key = self.api_key
        temperature = self.temperature
        model_name: str = self.model_name
        max_tokens = self.max_tokens
        model_kwargs = self.model_kwargs or {}
        base_url = self.base_url or "https://api.x.ai/v1"
        json_mode = self.json_mode
        seed = self.seed

        api_key = SecretStr(api_key).get_secret_value() if api_key else None

        output = ChatOpenAI(
            max_tokens=max_tokens or None,
            model_kwargs=model_kwargs,
            model=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature if temperature is not None else 0.1,
            seed=seed,
        )

        if json_mode:
            output = output.bind(response_format={"type": "json_object"})

        return output

    def _get_exception_message(self, e: Exception):
        """从 xAI/OpenAI 异常中提取可读信息。

        契约：输入异常对象；输出错误消息或 `None`；副作用无；
        失败语义：缺少 `openai` 依赖时返回 `None`。
        关键路径：1) 导入 `BadRequestError` 2) 提取 `e.body.message`。
        决策：仅处理 `BadRequestError`
        问题：其他异常类型缺少统一结构
        方案：缺省返回 `None` 交给上层处理
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
