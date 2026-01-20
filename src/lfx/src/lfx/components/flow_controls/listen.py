"""
模块名称：Listen 组件

本模块提供基于上下文键读取通知数据的组件，主要用于与 Notify 组件配合
实现异步通知传递。
主要功能包括：
- 从组件上下文读取指定键的数据

关键组件：
- `ListenComponent`：监听组件

设计背景：通过上下文共享实现简易通知机制。
注意事项：若上下文不存在指定键，将返回空 Data。
"""

from lfx.custom import Component
from lfx.io import Output, StrInput
from lfx.schema.data import Data


class ListenComponent(Component):
    """监听上下文键并输出 Data。

    契约：返回 `Data`；不存在时返回空 Data。
    """
    display_name = "Listen"
    description = "A component to listen for a notification."
    name = "Listen"
    beta: bool = True
    icon = "Radio"
    inputs = [
        StrInput(
            name="context_key",
            display_name="Context Key",
            info="The key of the context to listen for.",
            input_types=["Message"],
            required=True,
        )
    ]

    outputs = [Output(name="data", display_name="Data", method="listen_for_data", cache=False)]

    def listen_for_data(self) -> Data:
        """从上下文读取指定键对应的 Data。"""
        return self.ctx.get(self.context_key, Data(text=""))
