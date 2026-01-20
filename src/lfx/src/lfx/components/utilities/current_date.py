"""
模块名称：当前时间组件

本模块提供按时区返回当前日期时间的能力，主要用于在流程中生成带时区的时间戳。主要功能包括：
- 读取并展示可用时区列表
- 使用指定时区格式化当前时间
- 以 `Message` 形式返回结果或错误信息

关键组件：
- `CurrentDateComponent`：组件主体
- `get_current_date`：按时区获取当前时间

设计背景：在多地区场景中需要可配置时区的时间输出。
使用场景：日志标注、对话提示或时间戳生成。
注意事项：时区无效或系统不支持时会返回错误信息并记录日志。
"""

from datetime import datetime
from zoneinfo import ZoneInfo, available_timezones

from lfx.custom.custom_component.component import Component
from lfx.io import DropdownInput, Output
from lfx.log.logger import logger
from lfx.schema.message import Message


class CurrentDateComponent(Component):
    """当前时间获取组件。

    契约：输入 `timezone`；输出 `Message` 文本。
    副作用：记录状态与错误日志。
    失败语义：时区解析失败时返回错误消息并记录 debug 日志。
    决策：使用 `zoneinfo` 而非第三方时区库。
    问题：需要无额外依赖的时区转换能力。
    方案：采用标准库 `ZoneInfo` 与 `available_timezones()`。
    代价：受系统时区数据库影响，部分时区可能缺失。
    重评：当需要更完整时区数据或历史规则时引入第三方库。
    """
    display_name = "Current Date"
    description = "Returns the current date and time in the selected timezone."
    documentation: str = "https://docs.langflow.org/current-date"
    icon = "clock"
    name = "CurrentDate"

    inputs = [
        DropdownInput(
            name="timezone",
            display_name="Timezone",
            options=sorted(tz for tz in available_timezones() if tz != "localtime"),
            value="UTC",
            info="Select the timezone for the current date and time.",
            tool_mode=True,
        ),
    ]
    outputs = [
        Output(display_name="Current Date", name="current_date", method="get_current_date"),
    ]

    def get_current_date(self) -> Message:
        """返回指定时区的当前日期时间。

        契约：`timezone` 必须在 `available_timezones()` 内；返回格式 `YYYY-MM-DD HH:MM:SS TZ`。
        副作用：更新 `self.status`。
        失败语义：异常被捕获并返回错误消息文本。
        决策：异常转换为错误消息而非抛出。
        问题：时间组件应优先提供可读反馈。
        方案：捕获异常并返回 `Message(text=error)`。
        代价：调用方需主动判断错误文本。
        重评：当上游需要强失败信号时改为抛异常。
        """
        try:
            tz = ZoneInfo(self.timezone)
            current_date = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S %Z")
            result = f"Current date and time in {self.timezone}: {current_date}"
            self.status = result
            return Message(text=result)
        except Exception as e:  # noqa: BLE001
            logger.debug("Error getting current date", exc_info=True)
            error_message = f"Error: {e}"
            self.status = error_message
            return Message(text=error_message)
