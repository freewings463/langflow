"""
模块名称：Composio Gmail 组件

本模块提供 Composio 平台的 Gmail 接入组件，并包含常用动作的响应后处理。
主要功能包括：
- 绑定 `app_name` 为 `gmail`，用于匹配 Composio 工具包标识
- 为 `GMAIL_SEND_EMAIL` / `GMAIL_FETCH_EMAILS` 提供后处理逻辑

关键组件：
- ComposioGmailAPIComponent：Gmail 工具封装与响应规范化

设计背景：对 Gmail 常见动作进行结构化输出，降低下游解析成本。
注意事项：后处理仅作用于已注册的动作名，其余动作保持原始响应。
"""

from lfx.base.composio.composio_base import ComposioBaseComponent


class ComposioGmailAPIComponent(ComposioBaseComponent):
    """Gmail 的 Composio 组件封装。

    契约：`app_name` 必须与 Composio 工具包标识一致。
    副作用：注册特定动作的后处理函数。
    失败语义：后处理遇到未知结构时返回原始数据，不在此处抛错。
    """

    display_name: str = "Gmail"
    icon = "Gmail"
    documentation: str = "https://docs.composio.dev"
    app_name = "gmail"

    def __init__(self, **kwargs):
        """初始化组件并注册 Gmail 动作的后处理器。

        输入：`**kwargs` 透传给基类初始化。
        输出：无。
        副作用：写入 `self.post_processors`，用于规范化 Gmail 响应。
        """
        super().__init__(**kwargs)
        self.post_processors = {
            "GMAIL_SEND_EMAIL": self._process_send_email_response,
            "GMAIL_FETCH_EMAILS": self._process_fetch_emails_response,
        }

    def _process_send_email_response(self, raw_data):
        """规范化发送邮件动作的响应结构。

        输入：`raw_data`，通常为字典或包含 `response_data` 的字典。
        输出：提取后的 `message_id/thread_id/label_ids`；无法识别则返回原始数据。
        失败语义：不抛错，异常结构直接透传。
        """
        if isinstance(raw_data, dict):
            response_data = raw_data.get("response_data", raw_data)

            return {
                "message_id": response_data.get("id"),
                "thread_id": response_data.get("threadId"),
                "label_ids": response_data.get("labelIds", []),
            }
        return raw_data

    def _process_fetch_emails_response(self, raw_data):
        """规范化拉取邮件动作的响应结构。

        输入：`raw_data`，期望包含 `messages` 列表。
        输出：消息列表；结构不符合时返回原始数据。
        失败语义：不抛错，异常结构直接透传。
        """
        if isinstance(raw_data, dict):
            messages = raw_data.get("messages", [])
            if messages:
                return messages
        return raw_data

    def set_default_tools(self):
        """设置 Gmail 组件的默认工具列表。

        注意：当前未预置动作，需由用户在界面选择；如需默认动作请在此实现。
        """
