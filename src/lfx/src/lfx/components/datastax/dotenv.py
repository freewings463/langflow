"""
模块名称：Dotenv 加载组件

本模块提供将 .env 内容写入进程环境变量的组件封装。主要功能包括：
- 读取用户输入的 .env 内容并加载到环境变量
- 输出加载结果消息

关键组件：
- `Dotenv`

设计背景：在流程中注入凭证或环境配置以供后续组件使用。
使用场景：为 Assistants 或 AstraDB 组件提供运行时环境变量。
注意事项：使用 `override=True` 会覆盖已有环境变量。
"""

import io

from dotenv import load_dotenv

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import MultilineSecretInput
from lfx.schema.message import Message
from lfx.template.field.base import Output


class Dotenv(Component):
    """Dotenv 加载组件

    契约：输入 `.env` 文本内容；输出加载结果 `Message`；
    副作用：写入进程环境变量；
    失败语义：解析失败返回 `False` 并提示无变量。
    关键路径：1) 构造内存文件 2) 调用 `load_dotenv` 3) 返回结果消息。
    决策：使用 `override=True` 以确保输入内容生效。
    问题：默认环境变量可能与输入冲突。
    方案：允许输入覆盖。
    代价：可能覆盖已有配置导致行为变化。
    重评：当需要保留已有变量或提供合并策略时。
    """
    display_name = "Dotenv"
    description = "Load .env file into env vars"
    icon = "AstraDB"
    legacy = True
    inputs = [
        MultilineSecretInput(
            name="dotenv_file_content",
            display_name="Dotenv file content",
            info="Paste the content of your .env file directly, since contents are sensitive, "
            "using a Global variable set as 'password' is recommended",
        )
    ]

    outputs = [
        Output(display_name="env_set", name="env_set", method="process_inputs"),
    ]

    def process_inputs(self) -> Message:
        """加载 .env 并返回状态消息

        契约：返回 `Message` 描述加载结果；
        副作用：写入环境变量；
        失败语义：未读取到变量时返回提示消息。
        """
        fake_file = io.StringIO(self.dotenv_file_content)
        result = load_dotenv(stream=fake_file, override=True)

        message = Message(text="No variables found in .env")
        if result:
            message = Message(text="Loaded .env")
        return message
