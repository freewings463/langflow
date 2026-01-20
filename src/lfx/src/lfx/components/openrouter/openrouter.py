"""
模块名称：OpenRouter 模型组件

本模块提供 OpenRouter 的模型接入与动态模型列表加载，主要用于统一访问多家模型提供商。主要功能包括：
- 拉取 OpenRouter 模型列表并更新下拉选项
- 校验参数并构建 `ChatOpenAI` 实例
- 透传站点与应用信息以完善 OpenRouter 使用统计

关键组件：
- `OpenRouterComponent`：组件主体
- `fetch_models`：从 OpenRouter 获取模型清单
- `build_model`：构建 OpenRouter 语言模型实例

设计背景：通过 OpenRouter 聚合多模型，减少多套 SDK 的配置成本。
使用场景：在流程中选择 OpenRouter 模型并执行对话推理。
注意事项：模型列表请求超时为 10 秒；未选择模型或缺少 API Key 将抛 `ValueError`。
"""

import httpx
from langchain_openai import ChatOpenAI
from pydantic.v1 import SecretStr

from lfx.base.models.model import LCModelComponent
from lfx.field_typing import LanguageModel
from lfx.field_typing.range_spec import RangeSpec
from lfx.inputs.inputs import DropdownInput, IntInput, SecretStrInput, SliderInput, StrInput


class OpenRouterComponent(LCModelComponent):
    """OpenRouter 模型组件封装。

    契约：输入 `api_key`/`model_name`/`temperature` 等；输出 `LanguageModel` 实例。
    副作用：`fetch_models` 会发起网络请求；`build_model` 构建 LangChain 模型实例。
    失败语义：模型列表请求失败返回空列表并记录日志；缺少关键参数抛 `ValueError`。
    关键路径：1) 拉取模型列表并更新配置 2) 校验输入 3) 构建 `ChatOpenAI`。
    """

    display_name = "OpenRouter"
    description = (
        "OpenRouter provides unified access to multiple AI models from different providers through a single API."
    )
    icon = "OpenRouter"

    inputs = [
        *LCModelComponent.get_base_inputs(),
        SecretStrInput(name="api_key", display_name="API Key", required=True),
        DropdownInput(
            name="model_name",
            display_name="Model",
            options=[],
            value="",
            refresh_button=True,
            real_time_refresh=True,
            required=True,
        ),
        SliderInput(
            name="temperature",
            display_name="Temperature",
            value=0.7,
            range_spec=RangeSpec(min=0, max=2, step=0.01),
            advanced=True,
        ),
        IntInput(name="max_tokens", display_name="Max Tokens", advanced=True),
        StrInput(name="site_url", display_name="Site URL", advanced=True),
        StrInput(name="app_name", display_name="App Name", advanced=True),
    ]

    def fetch_models(self) -> list[dict]:
        """拉取可用模型列表并标准化输出。

        契约：返回包含 `id/name/context` 的列表；失败时返回空列表。
        副作用：发起 HTTP GET 请求（10 秒超时）。
        失败语义：网络错误或非 2xx 状态记录日志并返回空列表。
        """
        try:
            response = httpx.get("https://openrouter.ai/api/v1/models", timeout=10.0)
            response.raise_for_status()
            models = response.json().get("data", [])
            return sorted(
                [
                    {
                        "id": m["id"],
                        "name": m.get("name", m["id"]),
                        "context": m.get("context_length", 0),
                    }
                    for m in models
                    if m.get("id")
                ],
                key=lambda x: x["name"],
            )
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            self.log(f"Error fetching models: {e}")
            return []

    def update_build_config(self, build_config: dict, field_value: str, field_name: str | None = None) -> dict:  # noqa: ARG002
        """更新模型下拉选项与提示信息。

        契约：当模型列表可用时设置选项与 token 上限提示；失败时设置兜底选项。
        副作用：修改 `build_config` 内容。
        失败语义：`fetch_models` 失败则写入 "Failed to load models" 占位值。
        """
        models = self.fetch_models()
        if models:
            build_config["model_name"]["options"] = [m["id"] for m in models]
            build_config["model_name"]["tooltips"] = {m["id"]: f"{m['name']} ({m['context']:,} tokens)" for m in models}
        else:
            build_config["model_name"]["options"] = ["Failed to load models"]
            build_config["model_name"]["value"] = "Failed to load models"
        return build_config

    def build_model(self) -> LanguageModel:
        """构建 OpenRouter 的 LangChain 模型实例。

        契约：必须提供 `api_key` 与 `model_name`；返回 `ChatOpenAI` 兼容实例。
        副作用：无（仅构建对象，不发起模型调用）。
        失败语义：缺少 API Key 或模型名时抛 `ValueError`。
        """
        if not self.api_key:
            msg = "API key is required"
            raise ValueError(msg)
        if not self.model_name or self.model_name == "Loading...":
            msg = "Please select a model"
            raise ValueError(msg)

        kwargs = {
            "model": self.model_name,
            "openai_api_key": SecretStr(self.api_key).get_secret_value(),
            "openai_api_base": "https://openrouter.ai/api/v1",
            "temperature": self.temperature if self.temperature is not None else 0.7,
        }

        if self.max_tokens:
            kwargs["max_tokens"] = int(self.max_tokens)

        headers = {}
        if self.site_url:
            headers["HTTP-Referer"] = self.site_url
        if self.app_name:
            headers["X-Title"] = self.app_name
        if headers:
            kwargs["default_headers"] = headers

        return ChatOpenAI(**kwargs)
