"""
模块名称：Notify 组件

本模块提供向上下文写入通知数据的组件，主要用于与 Listen 组件配合
实现简单的通知传递机制。
主要功能包括：
- 将输入规范化为 Data 并写入上下文
- 支持追加或覆盖写入模式

关键组件：
- `NotifyComponent`：通知写入组件

设计背景：通过上下文共享实现跨节点通知。
注意事项：仅在图运行上下文中可用。
"""

from typing import cast

from lfx.custom import Component
from lfx.io import BoolInput, HandleInput, Output, StrInput
from lfx.schema.data import Data


class NotifyComponent(Component):
    """向上下文写入通知数据的组件。

    契约：返回写入的 `Data`；append 模式下会聚合为列表。
    副作用：更新上下文并触发状态顶点激活。
    失败语义：不在图中使用时抛 `ValueError`。
    """
    display_name = "Notify"
    description = "A component to generate a notification to Get Notified component."
    icon = "Notify"
    name = "Notify"
    beta: bool = True

    inputs = [
        StrInput(
            name="context_key",
            display_name="Context Key",
            info="The key of the context to store the notification.",
            required=True,
        ),
        HandleInput(
            name="input_value",
            display_name="Input Data",
            info="The data to store.",
            required=False,
            input_types=["Data", "Message", "DataFrame"],
        ),
        BoolInput(
            name="append",
            display_name="Append",
            info="If True, the record will be appended to the notification.",
            value=False,
            required=False,
        ),
    ]

    outputs = [
        Output(
            display_name="Data",
            name="result",
            method="notify_components",
            cache=False,
        ),
    ]

    async def notify_components(self) -> Data:
        """规范化输入并写入通知上下文。

        关键路径（三步）：
        1) 规范化输入为 `Data`
        2) 按 `append` 选择追加或覆盖写入
        3) 激活状态顶点以触发监听
        异常流：不在图内使用时抛 `ValueError`。
        """
        if not self._vertex:
            msg = "Notify component must be used in a graph."
            raise ValueError(msg)
        input_value: Data | str | dict | None = self.input_value
        if input_value is None:
            input_value = Data(text="")
        elif not isinstance(input_value, Data):
            if isinstance(input_value, str):
                input_value = Data(text=input_value)
            elif isinstance(input_value, dict):
                input_value = Data(data=input_value)
            else:
                input_value = Data(text=str(input_value))
        if input_value:
            if self.append:
                current_data = self.ctx.get(self.context_key, [])
                if not isinstance(current_data, list):
                    current_data = [current_data]
                current_data.append(input_value)
                self.update_ctx({self.context_key: current_data})
            else:
                self.update_ctx({self.context_key: input_value})
            self.status = input_value
        else:
            self.status = "No record provided."
        self._vertex.is_state = True
        self.graph.activate_state_vertices(name=self.context_key, caller=self._id)
        return cast("Data", input_value)
