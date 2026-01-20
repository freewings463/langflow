"""消息 schema。

本模块定义 Message 类型及其与 LangChain 的互转逻辑。
"""

from __future__ import annotations

import asyncio
import json
import re
import traceback
from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Annotated, Any, Literal
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from langchain_core.load import load
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_serializer, field_validator

if TYPE_CHECKING:
    from langchain_core.prompts.chat import BaseChatPromptTemplate

from lfx.base.prompts.utils import dict_values_to_string
from lfx.log.logger import logger
from lfx.schema.content_block import ContentBlock
from lfx.schema.content_types import ErrorContent
from lfx.schema.data import Data
from lfx.schema.image import Image, get_file_paths, is_image_file
from lfx.schema.properties import Properties, Source
from lfx.schema.validators import timestamp_to_str, timestamp_to_str_validator
from lfx.utils.constants import MESSAGE_SENDER_AI, MESSAGE_SENDER_NAME_AI, MESSAGE_SENDER_NAME_USER, MESSAGE_SENDER_USER
from lfx.utils.image import create_image_content_dict
from lfx.utils.mustache_security import safe_mustache_render

if TYPE_CHECKING:
    from lfx.schema.dataframe import DataFrame


class Message(Data):
    """消息模型。

    关键路径（三步）：
    1) 规范化文本、附件与属性字段；
    2) 支持与 LangChain Message 互转；
    3) 提供安全的 ID 访问方法。

    注意事项：消息 ID 仅在写入数据库后存在，需使用 `get_id/has_id/require_id` 访问。
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)
    # 图像数据辅助字段
    text_key: str = "text"
    text: str | AsyncIterator | Iterator | None = Field(default="")
    sender: str | None = None
    sender_name: str | None = None
    files: list[str | Image] | None = Field(default=[])
    session_id: str | UUID | None = Field(default="")
    context_id: str | UUID | None = Field(default="")
    timestamp: Annotated[str, timestamp_to_str_validator] = Field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z")
    )
    flow_id: str | UUID | None = None
    error: bool = Field(default=False)
    edit: bool = Field(default=False)

    properties: Properties = Field(default_factory=Properties)
    category: Literal["message", "error", "warning", "info"] | None = "message"
    content_blocks: list[ContentBlock] = Field(default_factory=list)
    duration: int | None = None

    @field_validator("flow_id", mode="before")
    @classmethod
    def validate_flow_id(cls, value):
        if isinstance(value, UUID):
            value = str(value)
        return value

    @field_validator("content_blocks", mode="before")
    @classmethod
    def validate_content_blocks(cls, value):
        # 注意：字符串可能以 "[" 开头
        if isinstance(value, list):
            return [
                ContentBlock.model_validate_json(v) if isinstance(v, str) else ContentBlock.model_validate(v)
                for v in value
            ]
        if isinstance(value, str):
            value = json.loads(value) if value.startswith("[") else [ContentBlock.model_validate_json(value)]
        return value

    @field_validator("properties", mode="before")
    @classmethod
    def validate_properties(cls, value):
        if isinstance(value, str):
            value = Properties.model_validate_json(value)
        elif isinstance(value, dict):
            value = Properties.model_validate(value)
        return value

    @field_serializer("flow_id")
    def serialize_flow_id(self, value):
        if isinstance(value, UUID):
            return str(value)
        return value

    @field_serializer("timestamp")
    def serialize_timestamp(self, value):
        try:
            # 尝试解析含时区
            return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S %Z").replace(tzinfo=timezone.utc)
        except ValueError:
            # 尝试解析不含时区
            return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)

    @field_validator("files", mode="before")
    @classmethod
    def validate_files(cls, value):
        if not value:
            value = []
        elif not isinstance(value, list):
            value = [value]
        return value

    def model_post_init(self, /, _context: Any) -> None:
        new_files: list[Any] = []
        for file in self.files or []:
            # 跳过已是 Image 实例的项
            if isinstance(file, Image):
                new_files.append(file)
            # 从 dict/对象中提取 path
            elif isinstance(file, dict) and "path" in file:
                file_path = file["path"]
                if file_path and is_image_file(file_path):
                    new_files.append(Image(path=file_path))
                else:
                    new_files.append(file_path if file_path else file)
            elif hasattr(file, "path") and file.path:
                if is_image_file(file.path):
                    new_files.append(Image(path=file.path))
                else:
                    new_files.append(file.path)
            elif isinstance(file, str) and is_image_file(file):
                new_files.append(Image(path=file))
            else:
                new_files.append(file)
        self.files = new_files
        if "timestamp" not in self.data:
            self.data["timestamp"] = self.timestamp

    def set_flow_id(self, flow_id: str) -> None:
        self.flow_id = flow_id

    def to_lc_message(
        self,
        model_name: str | None = None,
    ) -> BaseMessage:
        """转换为 LangChain BaseMessage。"""
        # 注意：根据 sender 决定 Human/AI，缺失则默认 Human
        if self.text is None or not self.sender:
            logger.warning("Missing required keys ('text', 'sender') in Message, defaulting to HumanMessage.")
        text = "" if not isinstance(self.text, str) else self.text

        if self.sender == MESSAGE_SENDER_USER or not self.sender:
            if self.files:
                contents = [{"type": "text", "text": text}]
                file_contents = self.get_file_content_dicts(model_name)
                contents.extend(file_contents)
                human_message = HumanMessage(content=contents)
            else:
                human_message = HumanMessage(content=text)
            return human_message

        return AIMessage(content=text)

    @classmethod
    def from_lc_message(cls, lc_message: BaseMessage) -> Message:
        if lc_message.type == "human":
            sender = MESSAGE_SENDER_USER
            sender_name = MESSAGE_SENDER_NAME_USER
        elif lc_message.type == "ai":
            sender = MESSAGE_SENDER_AI
            sender_name = MESSAGE_SENDER_NAME_AI
        elif lc_message.type == "system":
            sender = "System"
            sender_name = "System"
        elif lc_message.type == "tool":
            sender = "Tool"
            sender_name = "Tool"
        else:
            sender = lc_message.type
            sender_name = lc_message.type

        return cls(text=lc_message.content, sender=sender, sender_name=sender_name)

    @classmethod
    def from_data(cls, data: Data) -> Message:
        """将 Data 转换为 Message。"""
        return cls(
            text=data.text,
            sender=data.sender,
            sender_name=data.sender_name,
            files=data.files,
            session_id=data.session_id,
            context_id=data.context_id,
            timestamp=data.timestamp,
            flow_id=data.flow_id,
            error=data.error,
            edit=data.edit,
        )

    @field_serializer("text", mode="plain")
    def serialize_text(self, value):
        if isinstance(value, AsyncIterator | Iterator):
            return ""
        return value

    # 注意：为兼容旧版本保留该方法名
    def get_file_content_dicts(self, model_name: str | None = None):
        content_dicts = []
        try:
            files = get_file_paths(self.files)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Error getting file paths: {e}")
            return content_dicts

        for file in files:
            if isinstance(file, Image):
                # 注意：传入 flow_id 以解析相对路径
                content_dicts.append(file.to_content_dict(flow_id=self.flow_id))
            else:
                content_dicts.append(create_image_content_dict(file, None, model_name))
        return content_dicts

    def load_lc_prompt(self):
        if "prompt" not in self:
            msg = "Prompt is required."
            raise ValueError(msg)
        # 注意：prompt 经 jsonable_encoder 处理，内部消息需还原为 BaseMessage
        messages = []
        for message in self.prompt.get("kwargs", {}).get("messages", []):
            match message:
                case HumanMessage():
                    messages.append(message)
                case _ if message.get("type") == "human":
                    messages.append(HumanMessage(content=message.get("content")))
                case _ if message.get("type") == "system":
                    messages.append(SystemMessage(content=message.get("content")))
                case _ if message.get("type") == "ai":
                    messages.append(AIMessage(content=message.get("content")))
                case _ if message.get("type") == "tool":
                    messages.append(ToolMessage(content=message.get("content")))

        self.prompt["kwargs"]["messages"] = messages
        return load(self.prompt)

    @classmethod
    def from_lc_prompt(
        cls,
        prompt: BaseChatPromptTemplate,
    ):
        prompt_json = prompt.to_json()
        return cls(prompt=prompt_json)

    def format_text(self, template_format="f-string"):
        if template_format == "mustache":
            # 使用安全的 mustache 渲染器
            variables_with_str_values = dict_values_to_string(self.variables)
            formatted_prompt = safe_mustache_render(self.template, variables_with_str_values)
            self.text = formatted_prompt
            return formatted_prompt
        # 其他格式使用 LangChain 模板
        from langchain_core.prompts.prompt import PromptTemplate

        prompt_template = PromptTemplate.from_template(self.template, template_format=template_format)
        variables_with_str_values = dict_values_to_string(self.variables)
        formatted_prompt = prompt_template.format(**variables_with_str_values)
        self.text = formatted_prompt
        return formatted_prompt

    @classmethod
    async def from_template_and_variables(cls, template: str, template_format: str = "f-string", **variables):
        # 注意：为兼容旧版本保持异步
        return cls.from_template(template, template_format=template_format, **variables)

    # 注意：保留同步版本以兼容旧版本
    @classmethod
    def from_template(cls, template: str, template_format: str = "f-string", **variables):
        from langchain_core.prompts.chat import ChatPromptTemplate

        instance = cls(template=template, variables=variables)
        text = instance.format_text(template_format=template_format)
        message = HumanMessage(content=text)
        contents = []
        for value in variables.values():
            if isinstance(value, cls) and value.files:
                content_dicts = value.get_file_content_dicts()
                contents.extend(content_dicts)
        if contents:
            message = HumanMessage(content=[{"type": "text", "text": text}, *contents])

        prompt_template = ChatPromptTemplate.from_messages([message])

        instance.prompt = jsonable_encoder(prompt_template.to_json())
        instance.messages = instance.prompt.get("kwargs", {}).get("messages", [])
        return instance

    @classmethod
    async def create(cls, **kwargs):
        """若包含文件则在独立线程创建消息以避免阻塞。"""
        if kwargs.get("files"):
            return await asyncio.to_thread(cls, **kwargs)
        return cls(**kwargs)

    def to_data(self) -> Data:
        return Data(data=self.data)

    def to_dataframe(self) -> DataFrame:
        from lfx.schema.dataframe import DataFrame  # 本地导入避免循环

        return DataFrame(data=[self])

    def get_id(self) -> str | UUID | None:
        """安全获取消息 ID。"""
        return getattr(self, "id", None)

    def has_id(self) -> bool:
        """判断消息是否已有 ID。"""
        message_id = getattr(self, "id", None)
        return message_id is not None

    def require_id(self) -> str | UUID:
        """获取消息 ID，不存在则抛错。"""
        message_id = getattr(self, "id", None)
        if message_id is None:
            msg = "Message does not have an ID. Messages only have IDs after being stored in the database."
            raise ValueError(msg)
        return message_id


class DefaultModel(BaseModel):
    """默认序列化基类。"""
    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        json_encoders={
            datetime: lambda v: v.isoformat(),
            UUID: lambda v: str(v),
        },
    )

    def json(self, **kwargs):
        # 使用自定义序列化函数
        return super().model_dump_json(**kwargs, encoder=self.custom_encoder)

    @staticmethod
    def custom_encoder(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        msg = f"Object of type {obj.__class__.__name__} is not JSON serializable"
        raise TypeError(msg)


class MessageResponse(DefaultModel):
    """消息响应结构。"""
    id: str | UUID | None = Field(default=None)
    flow_id: UUID | None = Field(default=None)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    sender: str
    sender_name: str
    session_id: str
    context_id: str | None = None
    text: str
    files: list[str] = []
    edit: bool
    duration: float | None = None

    properties: Properties | None = None
    category: str | None = None
    content_blocks: list[ContentBlock] | None = None

    @field_validator("content_blocks", mode="before")
    @classmethod
    def validate_content_blocks(cls, v):
        if isinstance(v, str):
            v = json.loads(v)
        if isinstance(v, list):
            return [cls.validate_content_blocks(block) for block in v]
        if isinstance(v, dict):
            return ContentBlock.model_validate(v)
        return v

    @field_validator("properties", mode="before")
    @classmethod
    def validate_properties(cls, v):
        if isinstance(v, str):
            v = json.loads(v)
        return v

    @field_validator("files", mode="before")
    @classmethod
    def validate_files(cls, v):
        if isinstance(v, str):
            v = json.loads(v)
        return v

    @field_serializer("timestamp")
    @classmethod
    def serialize_timestamp(cls, v):
        return timestamp_to_str(v)

    @field_serializer("files")
    @classmethod
    def serialize_files(cls, v):
        if isinstance(v, list):
            return json.dumps(v)
        return v

    @classmethod
    def from_message(cls, message: Message, flow_id: str | None = None):
        # 先检查是否包含必需字段
        if message.text is None or not message.sender or not message.sender_name:
            msg = "The message does not have the required fields (text, sender, sender_name)."
            raise ValueError(msg)
        return cls(
            sender=message.sender,
            sender_name=message.sender_name,
            text=message.text,
            session_id=message.session_id,
            context_id=message.context_id,
            files=message.files or [],
            timestamp=message.timestamp,
            flow_id=flow_id,
        )


class ErrorMessage(Message):
    """错误消息模型。"""

    @staticmethod
    def _format_markdown_reason(exception: BaseException) -> str:
        """将异常信息格式化为 Markdown。"""
        reason = f"**{exception.__class__.__name__}**\n"
        if hasattr(exception, "body") and isinstance(exception.body, dict) and "message" in exception.body:
            reason += f" - **{exception.body.get('message')}**\n"
        elif hasattr(exception, "code"):
            reason += f" - **Code: {exception.code}**\n"
        elif hasattr(exception, "args") and exception.args:
            reason += f" - **Details: {exception.args[0]}**\n"
        elif isinstance(exception, ValidationError):
            reason += f" - **Details:**\n\n```python\n{exception!s}\n```\n"
        else:
            reason += " - **An unknown error occurred.**\n"
        return reason

    @staticmethod
    def _format_plain_reason(exception: BaseException) -> str:
        """将异常信息格式化为纯文本。"""
        if hasattr(exception, "body") and isinstance(exception.body, dict) and "message" in exception.body:
            reason = f"{exception.body.get('message')}\n"
        elif hasattr(exception, "_message"):
            reason = f"{exception._message()}\n" if callable(exception._message) else f"{exception._message}\n"  # noqa: SLF001
        elif hasattr(exception, "code"):
            reason = f"Code: {exception.code}\n"
        elif hasattr(exception, "args") and exception.args:
            reason = f"{exception.args[0]}\n"
        elif isinstance(exception, ValidationError):
            reason = f"{exception!s}\n"
        elif hasattr(exception, "detail"):
            reason = f"{exception.detail}\n"
        elif hasattr(exception, "message"):
            reason = f"{exception.message}\n"
        else:
            reason = "An unknown error occurred.\n"
        return reason

    def __init__(
        self,
        exception: BaseException,
        session_id: str | None = None,
        context_id: str | None = None,
        source: Source | None = None,
        trace_name: str | None = None,
        flow_id: UUID | str | None = None,
    ) -> None:
        # 注意：避免循环导入
        if exception.__class__.__name__ == "ExceptionWithMessageError" and exception.__cause__ is not None:
            exception = exception.__cause__

        plain_reason = self._format_plain_reason(exception)
        markdown_reason = self._format_markdown_reason(exception)
        # 获取发送者 ID
        if trace_name:
            match = re.search(r"\((.*?)\)", trace_name)
            if match:
                match.group(1)

        super().__init__(
            session_id=session_id,
            context_id=context_id,
            sender=source.display_name if source else None,
            sender_name=source.display_name if source else None,
            text=plain_reason,
            properties=Properties(
                text_color="red",
                background_color="red",
                edited=False,
                source=source,
                icon="error",
                allow_markdown=False,
                targets=[],
            ),
            category="error",
            error=True,
            content_blocks=[
                ContentBlock(
                    title="Error",
                    contents=[
                        ErrorContent(
                            type="error",
                            component=source.display_name if source else None,
                            field=str(exception.field) if hasattr(exception, "field") else None,
                            reason=markdown_reason,
                            solution=str(exception.solution) if hasattr(exception, "solution") else None,
                            traceback=traceback.format_exc(),
                        )
                    ],
                )
            ],
            flow_id=flow_id,
        )


__all__ = ["ContentBlock", "DefaultModel", "ErrorMessage", "Message", "MessageResponse"]
