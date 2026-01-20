"""
模块名称：`Icosa` 组合推理组件

本模块通过 `Icosa CR API` 调用组合优化服务，为输入提示词生成优化版本并输出原因列表。
主要功能包括：
- 向 `Icosa` 服务提交提示词与模型配置
- 返回优化后的提示词
- 提取并输出原因列表

关键组件：
- `CombinatorialReasonerComponent`

设计背景：将组合优化的提示词构造能力封装为 LangFlow 组件。
注意事项：需要 `OpenAI` Key 与 `Icosa` 账户凭证；请求失败会抛异常。
"""

import requests
from requests.auth import HTTPBasicAuth

from lfx.base.models.openai_constants import OPENAI_CHAT_MODEL_NAMES
from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import DropdownInput, SecretStrInput, StrInput
from lfx.io import MessageTextInput, Output
from lfx.schema.data import Data
from lfx.schema.message import Message


class CombinatorialReasonerComponent(Component):
    """`Icosa` 组合推理组件

    契约：
    - 输入：提示词、`OpenAI` Key、`Icosa` 账号与模型名
    - 输出：优化提示词 `Message` 与原因列表 `Data`
    - 副作用：调用外部 `Icosa` 服务
    - 失败语义：请求失败时抛出异常
    """
    display_name = "Combinatorial Reasoner"
    description = "Uses Combinatorial Optimization to construct an optimal prompt with embedded reasons. Sign up here:\nhttps://forms.gle/oWNv2NKjBNaqqvCx6"
    icon = "Icosa"
    name = "Combinatorial Reasoner"

    inputs = [
        MessageTextInput(name="prompt", display_name="Prompt", required=True),
        SecretStrInput(
            name="openai_api_key",
            display_name="OpenAI API Key",
            info="The OpenAI API Key to use for the OpenAI model.",
            advanced=False,
            value="OPENAI_API_KEY",
            required=True,
        ),
        StrInput(
            name="username",
            display_name="Username",
            info="Username to authenticate access to Icosa CR API",
            advanced=False,
            required=True,
        ),
        SecretStrInput(
            name="password",
            display_name="Combinatorial Reasoner Password",
            info="Password to authenticate access to Icosa CR API.",
            advanced=False,
            required=True,
        ),
        DropdownInput(
            name="model_name",
            display_name="Model Name",
            advanced=False,
            options=OPENAI_CHAT_MODEL_NAMES,
            value=OPENAI_CHAT_MODEL_NAMES[0],
        ),
    ]

    outputs = [
        Output(
            display_name="Optimized Prompt",
            name="optimized_prompt",
            method="build_prompt",
        ),
        Output(display_name="Selected Reasons", name="reasons", method="build_reasons"),
    ]

    def build_prompt(self) -> Message:
        """调用 `Icosa` 服务生成优化提示词

        关键路径（三步）：
        1) 组装请求参数与凭证
        2) 发起 `POST` 请求并校验响应
        3) 提取优化提示词与原因列表

        异常流：请求失败或响应格式异常时抛异常。
        性能瓶颈：外部请求延迟。
        排障入口：`requests` 异常信息。
        
        契约：
        - 输入：无（使用组件字段）
        - 输出：`Message` 或字符串（取决于服务响应）
        - 副作用：更新 `self.reasons`
        - 失败语义：请求失败时抛异常
        """
        params = {
            "prompt": self.prompt,
            "apiKey": self.openai_api_key,
            "model": self.model_name,
        }

        creds = HTTPBasicAuth(self.username, password=self.password)
        response = requests.post(
            "https://cr-api.icosacomputing.com/cr/langflow",
            json=params,
            auth=creds,
            timeout=100,
        )
        response.raise_for_status()

        prompt = response.json()["prompt"]

        self.reasons = response.json()["finalReasons"]
        return prompt

    def build_reasons(self) -> Data:
        """输出组合推理原因列表

        契约：
        - 输入：无（使用 `self.reasons`）
        - 输出：`Data`（原因列表）
        - 副作用：无
        - 失败语义：`self.reasons` 为空时返回空列表
        """
        # 注意：仅提取每条原因的首元素
        final_reasons = [reason[0] for reason in self.reasons]
        return Data(value=final_reasons)
