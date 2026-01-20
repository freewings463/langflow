"""
模块名称：Webhook 组件

本模块提供从外部 HTTP POST 接收 payload 的组件封装，主要用于在流程中
接入 Webhook 数据并转换为 `Data`。
主要功能包括：
- 解析 JSON payload 并构造 Data
- 在 JSON 无效时回退为原始文本

关键组件：
- `WebhookComponent`：Webhook 输入组件

设计背景：为外部系统提供简易接入入口。
注意事项：无效 JSON 会返回错误提示并将原始内容包装在 `payload` 字段。
"""

import json

from lfx.custom.custom_component.component import Component
from lfx.io import MultilineInput, Output
from lfx.schema.data import Data


class WebhookComponent(Component):
    """Webhook 输入组件。

    契约：`build_data` 返回 `Data`；解析失败时仍返回可用结构。
    副作用：更新 `self.status` 以供 UI 展示。
    """
    display_name = "Webhook"
    documentation: str = "https://docs.langflow.org/component-webhook"
    name = "Webhook"
    icon = "webhook"

    inputs = [
        MultilineInput(
            name="data",
            display_name="Payload",
            info="Receives a payload from external systems via HTTP POST.",
            advanced=True,
        ),
        MultilineInput(
            name="curl",
            display_name="cURL",
            value="CURL_WEBHOOK",
            advanced=True,
            input_types=[],
        ),
        MultilineInput(
            name="endpoint",
            display_name="Endpoint",
            value="BACKEND_URL",
            advanced=False,
            copy_field=True,
            input_types=[],
        ),
    ]
    outputs = [
        Output(display_name="Data", name="output_data", method="build_data"),
    ]

    def build_data(self) -> Data:
        """解析 payload 并构建 `Data`。

        关键路径（三步）：
        1) 校验是否为空数据
        2) 尝试 JSON 解析，失败则回退
        3) 构建 Data 并更新状态
        异常流：JSON 解析失败不抛异常，转为提示信息。
        """
        message: str | Data = ""
        if not self.data:
            self.status = "No data provided."
            return Data(data={})
        try:
            my_data = self.data.replace('"\n"', '"\\n"')
            body = json.loads(my_data or "{}")
        except json.JSONDecodeError:
            body = {"payload": self.data}
            message = f"Invalid JSON payload. Please check the format.\n\n{self.data}"
        data = Data(data=body)
        if not message:
            message = data
        self.status = message
        return data
