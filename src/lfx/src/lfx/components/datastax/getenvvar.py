"""
模块名称：环境变量读取组件

本模块提供读取系统环境变量的组件封装。主要功能包括：
- 根据变量名读取环境变量值
- 输出读取结果

关键组件：
- `GetEnvVar`

设计背景：需要在流程中读取已注入的环境变量。
使用场景：与 Dotenv 或外部配置结合使用。
注意事项：变量不存在会抛 `ValueError`。
"""

import os

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import StrInput
from lfx.schema.message import Message
from lfx.template.field.base import Output


class GetEnvVar(Component):
    """环境变量读取组件

    契约：输入变量名；输出变量值 `Message`；
    副作用：无；失败语义：变量不存在抛 `ValueError`。
    关键路径：1) 校验变量是否存在 2) 返回值。
    决策：变量不存在时直接抛错而非返回空。
    问题：隐式空值会掩盖配置问题。
    方案：显式失败。
    代价：需要调用方处理异常。
    重评：当需要容错或默认值策略时。
    """
    display_name = "Get Environment Variable"
    description = "Gets the value of an environment variable from the system."
    icon = "AstraDB"
    legacy = True

    inputs = [
        StrInput(
            name="env_var_name",
            display_name="Environment Variable Name",
            info="Name of the environment variable to get",
        )
    ]

    outputs = [
        Output(display_name="Environment Variable Value", name="env_var_value", method="process_inputs"),
    ]

    def process_inputs(self) -> Message:
        """读取环境变量并返回

        契约：返回 `Message` 包含变量值；
        副作用：无；失败语义：变量不存在抛 `ValueError`。
        """
        if self.env_var_name not in os.environ:
            msg = f"Environment variable {self.env_var_name} not set"
            raise ValueError(msg)
        return Message(text=os.environ[self.env_var_name])
