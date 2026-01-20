"""模块名称：通用响应与文件类型 Schema

模块目的：统一对外输出的响应结构与文件类型约束。
主要功能：
- 文件类型白名单与图片类型集合
- Chat/Data 输出模型及校验
- 自定义 Enum 包含判断
使用场景：后端输出序列化与前端消费。
关键组件：`ChatOutputResponse`、`DataOutputResponse`、`File`
设计背景：统一输出结构，便于前端/服务间契约稳定。
注意事项：`validate_message` 会对 AI 消息做换行规范化。
"""

import enum

from langchain_core.messages import BaseMessage
from pydantic import BaseModel, field_validator, model_validator
from typing_extensions import TypedDict

from .constants import MESSAGE_SENDER_AI, MESSAGE_SENDER_NAME_AI

# 注意：文件类型列表从 `lfx.base.data.utils` 迁移而来
TEXT_FILE_TYPES = [
    "txt",
    "md",
    "mdx",
    "csv",
    "json",
    "yaml",
    "yml",
    "xml",
    "html",
    "htm",
    "pdf",
    "docx",
    "py",
    "sh",
    "sql",
    "js",
    "ts",
    "tsx",
]
IMG_FILE_TYPES = ["jpg", "jpeg", "png", "bmp", "image"]


class File(TypedDict):
    """文件结构约定（前端消费）。"""

    path: str
    name: str
    type: str


class ChatOutputResponse(BaseModel):
    """聊天输出响应结构。"""

    message: str | list[str | dict]
    sender: str | None = MESSAGE_SENDER_AI
    sender_name: str | None = MESSAGE_SENDER_NAME_AI
    session_id: str | None = None
    stream_url: str | None = None
    component_id: str | None = None
    files: list[File] = []
    type: str

    @field_validator("files", mode="before")
    @classmethod
    def validate_files(cls, files):
        """校验并补全文件元信息。"""
        if not files:
            return files

        for file in files:
            if not isinstance(file, dict):
                msg = "Files must be a list of dictionaries."
                raise ValueError(msg)  # noqa: TRY004

            if not all(key in file for key in ["path", "name", "type"]):
                # 注意：缺失字段时从路径推断 `name/type`。
                path = file.get("path")
                if not path:
                    msg = "File path is required."
                    raise ValueError(msg)

                name = file.get("name")
                if not name:
                    name = path.split("/")[-1]
                    file["name"] = name
                type_ = file.get("type")
                if not type_:
                    # 从路径/扩展名推断文件类型
                    extension = path.split(".")[-1]
                    file_types = set(TEXT_FILE_TYPES + IMG_FILE_TYPES)
                    if extension and extension in file_types:
                        type_ = extension
                    else:
                        for file_type in file_types:
                            if file_type in path:
                                type_ = file_type
                                break
                    if not type_:
                        msg = "File type is required."
                        raise ValueError(msg)
                file["type"] = type_

        return files

    @classmethod
    def from_message(
        cls,
        message: BaseMessage,
        sender: str | None = MESSAGE_SENDER_AI,
        sender_name: str | None = MESSAGE_SENDER_NAME_AI,
    ):
        """从 `BaseMessage` 构建响应对象。"""
        content = message.content
        return cls(message=content, sender=sender, sender_name=sender_name)

    @model_validator(mode="after")
    def validate_message(self):
        """规范化 AI 消息中的换行，确保 Markdown 兼容。"""

        if self.sender != MESSAGE_SENDER_AI:
            return self

        # 注意：先压平再扩展，避免重复换行。
        message = self.message.replace("\n\n", "\n")
        self.message = message.replace("\n", "\n\n")
        return self


class DataOutputResponse(BaseModel):
    """数据输出响应结构。"""

    data: list[dict | None]


class ContainsEnumMeta(enum.EnumMeta):
    """为 Enum 提供 `in` 语义的元类辅助。"""

    def __contains__(cls, item) -> bool:
        try:
            cls(item)
        except ValueError:
            return False
        else:
            return True
