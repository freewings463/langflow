"""Message 转 Data 组件。

本模块将 Message 对象转换为 Data，用于兼容下游组件。
设计背景：旧组件保留以兼容历史流程。
注意事项：通过属性判断 Message 类型以兼容多实现。
"""

from lfx.custom.custom_component.component import Component
from lfx.io import MessageInput, Output
from lfx.log.logger import logger
from lfx.schema.data import Data


class MessageToDataComponent(Component):
    """Message 转 Data 组件封装。

    契约：输入为 Message；输出为 Data。
    副作用：更新 `self.status`。
    失败语义：输入不符合 Message 结构时返回错误 Data。
    """
    display_name = "Message to Data"
    description = "Convert a Message object to a Data object"
    icon = "message-square-share"
    beta = True
    name = "MessagetoData"
    legacy = True
    replacement = ["processing.TypeConverterComponent"]

    inputs = [
        MessageInput(
            name="message",
            display_name="Message",
            info="The Message object to convert to a Data object",
        ),
    ]

    outputs = [
        Output(display_name="Data", name="data", method="convert_message_to_data"),
    ]

    def convert_message_to_data(self) -> Data:
        """将 Message 转换为 Data。"""
        # 注意：通过属性判断兼容不同 Message 实现
        if hasattr(self.message, "data") and hasattr(self.message, "text") and hasattr(self.message, "get_text"):
            return Data(data=self.message.data)

        msg = "Error converting Message to Data: Input must be a Message object"
        logger.debug(msg, exc_info=True)
        self.status = msg
        return Data(data={"error": msg})
