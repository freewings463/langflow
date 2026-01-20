"""
模块名称：`Gmail` 加载组件

本模块提供 `GmailLoaderComponent`，用于通过 `OAuth` 凭证加载 `Gmail` 邮件并转换为对话数据。
主要功能包括：
- 解析 `Gmail API` 返回的邮件内容
- 清洗文本并构建 `ChatSession`
- 输出为 `Data` 结构供下游使用

关键组件：`GmailLoaderComponent`
设计背景：将 `Gmail` 邮件加载能力集成到 `Langflow` 组件体系
注意事项：依赖 `Google API` 凭证；仅处理 `text/plain` 部分
"""

import base64
import json
import re
from collections.abc import Iterator
from json.decoder import JSONDecodeError
from typing import Any

from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from langchain_core.chat_sessions import ChatSession
from langchain_core.messages import HumanMessage
from langchain_google_community.gmail.loader import GMailLoader

from lfx.custom.custom_component.component import Component
from lfx.inputs.inputs import MessageTextInput
from lfx.io import SecretStrInput
from lfx.log.logger import logger
from lfx.schema.data import Data
from lfx.template.field.base import Output


class GmailLoaderComponent(Component):
    """`Gmail` 邮件加载组件。
    契约：输入为 `OAuth` 凭证与标签/数量；输出为 `Data`。
    关键路径：解析凭证 → 构建 `loader` → 拉取邮件 → 组装 `ChatSession`。
    决策：仅解析 `text/plain` 部分。问题：`HTML` 内容复杂；方案：只取纯文本；代价：丢失富文本信息；重评：当需要 `HTML` 渲染时。
    """

    display_name = "Gmail Loader"
    description = "Loads emails from Gmail using provided credentials."
    icon = "Google"
    legacy: bool = True
    replacement = ["composio.ComposioGmailAPIComponent"]

    inputs = [
        SecretStrInput(
            name="json_string",
            display_name="JSON String of the Service Account Token",
            info="JSON string containing OAuth 2.0 access token information for service account access",
            required=True,
            value="""{
                "account": "",
                "client_id": "",
                "client_secret": "",
                "expiry": "",
                "refresh_token": "",
                "scopes": [
                    "https://www.googleapis.com/auth/gmail.readonly",
                ],
                "token": "",
                "token_uri": "https://oauth2.googleapis.com/token",
                "universe_domain": "googleapis.com"
            }""",
        ),
        MessageTextInput(
            name="label_ids",
            display_name="Label IDs",
            info="Comma-separated list of label IDs to filter emails.",
            required=True,
            value="INBOX,SENT,UNREAD,IMPORTANT",
        ),
        MessageTextInput(
            name="max_results",
            display_name="Max Results",
            info="Maximum number of emails to load.",
            required=True,
            value="10",
        ),
    ]

    outputs = [
        Output(display_name="Data", name="data", method="load_emails"),
    ]

    def load_emails(self) -> Data:
        """加载邮件并返回 `Data`。
        契约：返回 `Data(data={"text": docs})`；认证失败抛 `ValueError`。
        关键路径：解析凭证 → 构建 `loader` → 拉取邮件 → 返回结果。
        决策：无消息时仅记录告警。问题：避免空结果中断流程；方案：警告日志；代价：下游需处理空结果；重评：当需要强失败语义时。
        """
        class CustomGMailLoader(GMailLoader):
            """自定义 `GMailLoader`，支持标签过滤与内容清洗。
            契约：输入凭证/标签/数量，输出 `ChatSession` 迭代器。
            关键路径：加载邮件 → 提取内容 → 构建会话。
            决策：默认标签为 `SENT`。问题：避免空标签；方案：默认值；代价：结果可能偏差；重评：当需要完全由用户指定时。
            """

            def __init__(
                self, creds: Any, *, n: int = 100, label_ids: list[str] | None = None, raise_error: bool = False
            ) -> None:
                """初始化自定义 `loader`。
                契约：保存 `creds`/`label_ids`/`n` 并初始化父类。
                关键路径：调用父类构造 → 设置标签列表。
                决策：未传 `label_ids` 时默认 `SENT`。问题：避免空标签；方案：默认值；代价：结果偏差；重评：当需要完全由用户指定时。
                """
                super().__init__(creds, n, raise_error)
                self.label_ids = label_ids if label_ids is not None else ["SENT"]

            def clean_message_content(self, message):
                """清洗邮件正文文本。
                契约：返回去除 `URL`/邮箱/特殊字符后的文本。
                关键路径：正则移除 `URL` → 移除邮箱 → 过滤特殊字符 → 去空格。
                决策：仅保留字母数字空白。问题：简化对话输入；方案：正则过滤；代价：丢失格式信息；重评：当需要保留标点时。
                """
                # 注意：移除 `URL`。
                message = re.sub(r"http\S+|www\S+|https\S+", "", message, flags=re.MULTILINE)

                # 注意：移除邮箱地址。
                message = re.sub(r"\S+@\S+", "", message)

                # 注意：移除特殊字符并压缩空白。
                message = re.sub(r"[^A-Za-z0-9\s]+", " ", message)
                message = re.sub(r"\s{2,}", " ", message)

                # 注意：去掉首尾空白。
                return message.strip()

            def _extract_email_content(self, msg: Any) -> HumanMessage:
                """从 `Gmail` 消息中提取纯文本内容。
                契约：返回 `HumanMessage`；缺失 `From` 或纯文本部分时抛 `ValueError`。
                关键路径：读取 `headers` → 定位 `text/plain` → 解码内容 → 清洗文本。
                决策：仅取最新回复片段。问题：避免引用内容干扰；方案：正则截断；代价：可能丢失上下文；重评：当需要完整线程时。
                """
                from_email = None
                for values in msg["payload"]["headers"]:
                    name = values["name"]
                    if name == "From":
                        from_email = values["value"]
                if from_email is None:
                    msg = "From email not found."
                    raise ValueError(msg)

                parts = msg["payload"]["parts"] if "parts" in msg["payload"] else [msg["payload"]]

                for part in parts:
                    if part["mimeType"] == "text/plain":
                        data = part["body"]["data"]
                        data = base64.urlsafe_b64decode(data).decode("utf-8")
                        pattern = re.compile(r"\r\nOn .+(\r\n)*wrote:\r\n")
                        newest_response = re.split(pattern, data)[0]
                        return HumanMessage(
                            content=self.clean_message_content(newest_response),
                            additional_kwargs={"sender": from_email},
                        )
                msg = "No plain text part found in the email."
                raise ValueError(msg)

            def _get_message_data(self, service: Any, message: Any) -> ChatSession:
                """根据 `message_id` 构建 `ChatSession`。
                契约：返回包含当前消息与可能的首封邮件的会话。
                关键路径：获取 message → 解析 In-Reply-To → 关联线程 → 构建会话。
                决策：若无 In-Reply-To，仅返回单条消息会话。问题：简化流程；方案：单条会话；代价：丢失上下文；重评：当需要完整对话链路时。
                """
                msg = service.users().messages().get(userId="me", id=message["id"]).execute()
                message_content = self._extract_email_content(msg)

                in_reply_to = None
                email_data = msg["payload"]["headers"]
                for values in email_data:
                    name = values["name"]
                    if name == "In-Reply-To":
                        in_reply_to = values["value"]

                thread_id = msg["threadId"]

                if in_reply_to:
                    thread = service.users().threads().get(userId="me", id=thread_id).execute()
                    messages = thread["messages"]

                    response_email = None
                    for _message in messages:
                        email_data = _message["payload"]["headers"]
                        for values in email_data:
                            if values["name"] == "Message-ID":
                                message_id = values["value"]
                                if message_id == in_reply_to:
                                    response_email = _message
                    if response_email is None:
                        msg = "Response email not found in the thread."
                        raise ValueError(msg)
                    starter_content = self._extract_email_content(response_email)
                    return ChatSession(messages=[starter_content, message_content])
                return ChatSession(messages=[message_content])

            def lazy_load(self) -> Iterator[ChatSession]:
                """惰性加载邮件会话。
                契约：返回 `ChatSession` 迭代器；单条失败不会中断（除非 `raise_error`）。
                关键路径：列举消息 → 逐条解析 → yield 会话。
                决策：默认吞掉单条异常。问题：避免全量失败；方案：记录日志；代价：可能丢失部分邮件；重评：当需要强一致性时。
                """
                service = build("gmail", "v1", credentials=self.creds)
                results = (
                    service.users().messages().list(userId="me", labelIds=self.label_ids, maxResults=self.n).execute()
                )
                messages = results.get("messages", [])
                if not messages:
                    logger.warning("No messages found with the specified labels.")
                for message in messages:
                    try:
                        yield self._get_message_data(service, message)
                    except Exception:
                        if self.raise_error:
                            raise
                        else:
                            logger.exception(f"Error processing message {message['id']}")

        json_string = self.json_string
        label_ids = self.label_ids.split(",") if self.label_ids else ["INBOX"]
        max_results = int(self.max_results) if self.max_results else 100

        # 注意：从 `JSON` 字符串解析凭证信息。
        try:
            token_info = json.loads(json_string)
        except JSONDecodeError as e:
            msg = "Invalid JSON string"
            raise ValueError(msg) from e

        creds = Credentials.from_authorized_user_info(token_info)

        # 注意：使用凭证初始化自定义 `loader`。
        loader = CustomGMailLoader(creds=creds, n=max_results, label_ids=label_ids)

        try:
            docs = loader.load()
        except RefreshError as e:
            msg = "Authentication error: Unable to refresh authentication token. Please try to reauthenticate."
            raise ValueError(msg) from e
        except Exception as e:
            msg = f"Error loading documents: {e}"
            raise ValueError(msg) from e

        # 注意：返回加载到的会话数据。
        self.status = docs
        return Data(data={"text": docs})
