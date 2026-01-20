"""
模块名称：huggingface

本模块提供 Hugging Face Inference Endpoints 组件封装。
主要功能包括：
- 组装模型调用参数并创建 Endpoint 客户端
- 支持自定义模型与重试策略

关键组件：
- `HuggingFaceEndpointsComponent`：Endpoints 组件

设计背景：需要在 Langflow 中使用 Hugging Face 推理端点
使用场景：文本生成与摘要等任务
注意事项：旧版 `langchain_community` 接口已弃用，后续需迁移
"""

from typing import Any

from langchain_community.llms.huggingface_endpoint import HuggingFaceEndpoint
from tenacity import retry, stop_after_attempt, wait_fixed

from lfx.base.models.model import LCModelComponent
from lfx.field_typing import LanguageModel
from lfx.field_typing.range_spec import RangeSpec
from lfx.io import DictInput, DropdownInput, FloatInput, IntInput, SecretStrInput, SliderInput, StrInput

# 注意：`langchain_community.llms.huggingface_endpoint` 已弃用，待依赖升级后迁移。

DEFAULT_MODEL = "meta-llama/Llama-3.3-70B-Instruct"


class HuggingFaceEndpointsComponent(LCModelComponent):
    """Hugging Face Endpoints 组件。

    契约：需提供 `huggingfacehub_api_token` 与 `model_id` 或自定义模型。
    副作用：创建 Endpoint 客户端实例。
    失败语义：初始化失败抛 `ValueError`。
    决策：对 Hugging Face 域名自动拼接模型路径。
    问题：用户可能只输入模型 ID 或完整 endpoint URL。
    方案：检测域名并拼接或直接使用。
    代价：对自定义域名需完整 URL。
    重评：当 SDK 统一接收模型 ID 时。
    """
    display_name: str = "Hugging Face"
    description: str = "Generate text using Hugging Face Inference APIs."
    icon = "HuggingFace"
    name = "HuggingFaceModel"

    inputs = [
        *LCModelComponent.get_base_inputs(),
        DropdownInput(
            name="model_id",
            display_name="Model ID",
            info="Select a model from Hugging Face Hub",
            options=[
                DEFAULT_MODEL,
                "mistralai/Mixtral-8x7B-Instruct-v0.1",
                "mistralai/Mistral-7B-Instruct-v0.3",
                "meta-llama/Llama-3.1-8B-Instruct",
                "Qwen/Qwen2.5-Coder-32B-Instruct",
                "Qwen/QwQ-32B-Preview",
                "openai-community/gpt2",
                "custom",
            ],
            value=DEFAULT_MODEL,
            required=True,
            real_time_refresh=True,
        ),
        StrInput(
            name="custom_model",
            display_name="Custom Model ID",
            info="Enter a custom model ID from Hugging Face Hub",
            value="",
            show=False,
            required=True,
        ),
        IntInput(
            name="max_new_tokens", display_name="Max New Tokens", value=512, info="Maximum number of generated tokens"
        ),
        IntInput(
            name="top_k",
            display_name="Top K",
            advanced=True,
            info="The number of highest probability vocabulary tokens to keep for top-k-filtering",
        ),
        FloatInput(
            name="top_p",
            display_name="Top P",
            value=0.95,
            advanced=True,
            info=(
                "If set to < 1, only the smallest set of most probable tokens with "
                "probabilities that add up to `top_p` or higher are kept for generation"
            ),
        ),
        FloatInput(
            name="typical_p",
            display_name="Typical P",
            value=0.95,
            advanced=True,
            info="Typical Decoding mass.",
        ),
        SliderInput(
            name="temperature",
            display_name="Temperature",
            value=0.8,
            range_spec=RangeSpec(min=0, max=2, step=0.01),
            info="The value used to module the logits distribution",
            advanced=True,
        ),
        FloatInput(
            name="repetition_penalty",
            display_name="Repetition Penalty",
            info="The parameter for repetition penalty. 1.0 means no penalty.",
            advanced=True,
        ),
        StrInput(
            name="inference_endpoint",
            display_name="Inference Endpoint",
            value="https://api-inference.huggingface.co/models/",
            info="Custom inference endpoint URL.",
            required=True,
        ),
        DropdownInput(
            name="task",
            display_name="Task",
            options=["text2text-generation", "text-generation", "summarization", "translation"],
            value="text-generation",
            advanced=True,
            info="The task to call the model with. Should be a task that returns `generated_text` or `summary_text`.",
        ),
        SecretStrInput(
            name="huggingfacehub_api_token", display_name="HuggingFace HubAPI Token", password=True, required=True
        ),
        DictInput(name="model_kwargs", display_name="Model Keyword Arguments", advanced=True),
        IntInput(name="retry_attempts", display_name="Retry Attempts", value=1, advanced=True),
    ]

    def get_api_url(self) -> str:
        """构造 API URL。

        契约：当选择 `custom` 时必须提供 `custom_model`。
        失败语义：缺少自定义模型时抛 `ValueError`。
        """
        if "huggingface" in self.inference_endpoint.lower():
            if self.model_id == "custom":
                if not self.custom_model:
                    error_msg = "Custom model ID is required when 'custom' is selected"
                    raise ValueError(error_msg)
                return f"{self.inference_endpoint}{self.custom_model}"
            return f"{self.inference_endpoint}{self.model_id}"
        return self.inference_endpoint

    async def update_build_config(self, build_config: dict, field_value: Any, field_name: str | None = None) -> dict:
        """根据模型选择更新自定义字段显示。

        契约：仅修改 `build_config` 显示/必填标记。
        失败语义：异常时记录日志但不中断流程。
        关键路径（三步）：1) 判断字段变更 2) 切换显示 3) 返回配置。
        """
        try:
            if field_name is None or field_name == "model_id":
                if field_value == "custom":
                    build_config["custom_model"]["show"] = True
                    build_config["custom_model"]["required"] = True
                else:
                    build_config["custom_model"]["show"] = False
                    build_config["custom_model"]["value"] = ""

        except (KeyError, AttributeError) as e:
            self.log(f"Error updating build config: {e!s}")
        return build_config

    def create_huggingface_endpoint(
        self,
        task: str | None,
        huggingfacehub_api_token: str | None,
        model_kwargs: dict[str, Any],
        max_new_tokens: int,
        top_k: int | None,
        top_p: float,
        typical_p: float | None,
        temperature: float | None,
        repetition_penalty: float | None,
    ) -> HuggingFaceEndpoint:
        """创建 Hugging Face Endpoint 客户端。

        契约：`retry_attempts` 次重试后仍失败则抛异常。
        副作用：可能发起网络连接。
        失败语义：底层异常透传。
        """
        retry_attempts = self.retry_attempts
        endpoint_url = self.get_api_url()

        @retry(stop=stop_after_attempt(retry_attempts), wait=wait_fixed(2))
        def _attempt_create():
            return HuggingFaceEndpoint(
                endpoint_url=endpoint_url,
                task=task,
                huggingfacehub_api_token=huggingfacehub_api_token,
                model_kwargs=model_kwargs,
                max_new_tokens=max_new_tokens,
                top_k=top_k,
                top_p=top_p,
                typical_p=typical_p,
                temperature=temperature,
                repetition_penalty=repetition_penalty,
            )

        return _attempt_create()

    def build_model(self) -> LanguageModel:
        """构建并返回 Hugging Face 语言模型实例。

        契约：返回实现 `LanguageModel` 的对象。
        副作用：创建 Endpoint 客户端并可能触发网络请求。
        失败语义：连接失败抛 `ValueError`。
        关键路径（三步）：1) 读取输入参数 2) 构建 Endpoint 3) 返回实例。
        决策：对可选参数做 `None` 回退处理。
        问题：UI 可能传空值导致 SDK 类型错误。
        方案：将空值转换为 `None` 或默认值。
        代价：无法区分用户显式设置空值与未设置。
        重评：当配置层强约束参数时。
        """
        task = self.task or None
        huggingfacehub_api_token = self.huggingfacehub_api_token
        model_kwargs = self.model_kwargs or {}
        max_new_tokens = self.max_new_tokens
        top_k = self.top_k or None
        top_p = self.top_p
        typical_p = self.typical_p or None
        temperature = self.temperature or 0.8
        repetition_penalty = self.repetition_penalty or None

        try:
            llm = self.create_huggingface_endpoint(
                task=task,
                huggingfacehub_api_token=huggingfacehub_api_token,
                model_kwargs=model_kwargs,
                max_new_tokens=max_new_tokens,
                top_k=top_k,
                top_p=top_p,
                typical_p=typical_p,
                temperature=temperature,
                repetition_penalty=repetition_penalty,
            )
        except Exception as e:
            msg = "Could not connect to Hugging Face Endpoints API."
            raise ValueError(msg) from e

        return llm
