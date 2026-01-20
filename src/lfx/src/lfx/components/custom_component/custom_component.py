"""
模块名称：custom_component

本模块提供自定义组件模板，用于快速创建新的 Langflow 组件。
主要功能包括：
- 定义最小输入输出结构
- 演示 `Component` 的基本用法

关键组件：
- `CustomComponent`：示例组件

设计背景：为用户提供可复制的组件起点
使用场景：新组件开发与快速验证
注意事项：该组件仅用于模板示例
"""

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output
from lfx.schema.data import Data


class CustomComponent(Component):
    """自定义组件模板。

    契约：输入 `input_value` 字符串；输出 `Data(value=...)`。
    副作用：设置 `self.status` 为输出数据。
    失败语义：无显式异常；类型错误由框架校验抛出。
    """
    display_name = "Custom Component"
    description = "Use as a template to create your own component."
    documentation: str = "https://docs.langflow.org/components-custom-components"
    icon = "code"
    name = "CustomComponent"

    inputs = [
        MessageTextInput(
            name="input_value",
            display_name="Input Value",
            info="This is a custom component Input",
            value="Hello, World!",
            tool_mode=True,
        ),
    ]

    outputs = [
        Output(display_name="Output", name="output", method="build_output"),
    ]

    def build_output(self) -> Data:
        """构建并返回输出数据。

        契约：输出 `Data`，字段 `value` 与输入一致。
        副作用：更新 `status` 供 UI 展示。
        失败语义：无显式异常。
        """
        data = Data(value=self.input_value)
        self.status = data
        return data
