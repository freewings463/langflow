"""
模块名称：schemas

本模块定义了各种数据模式，主要用于聊天和数据输出的结构化表示。
主要功能包括：
- 定义文件模式
- 定义聊天输出响应模式
- 定义数据输出响应模式
- 提供枚举类型的容器类

设计背景：在聊天应用中需要标准化的数据结构来处理消息、文件和响应
注意事项：使用Pydantic模型进行数据验证
"""

import enum

from langchain_core.messages import BaseMessage
from lfx.base.data.utils import IMG_FILE_TYPES, TEXT_FILE_TYPES
from lfx.utils.constants import MESSAGE_SENDER_AI, MESSAGE_SENDER_NAME_AI
from pydantic import BaseModel, field_validator, model_validator
from typing_extensions import TypedDict


class File(TypedDict):
    """File schema."""

    path: str
    name: str
    type: str


class ChatOutputResponse(BaseModel):
    """聊天输出响应模式。
    
    用于定义聊天应用中输出响应的标准结构，包括消息内容、发送者信息、文件等。
    """

    message: str | list[str | dict]  # 消息内容，可以是字符串或字符串和字典的列表
    sender: str | None = MESSAGE_SENDER_AI  # 发送者标识，默认为AI
    sender_name: str | None = MESSAGE_SENDER_NAME_AI  # 发送者名称，默认为AI名称
    session_id: str | None = None  # 会话ID
    stream_url: str | None = None  # 流媒体URL
    component_id: str | None = None  # 组件ID
    files: list[File] = []  # 文件列表
    type: str  # 消息类型

    @field_validator("files", mode="before")
    @classmethod
    def validate_files(cls, files):
        """验证文件列表。
        
        关键路径（三步）：
        1) 检查文件列表是否为空
        2) 验证每个文件是否为字典类型并包含必需的键
        3) 补全缺失的文件信息（从路径推断名称和类型）
        
        异常流：
        - 如果文件不是字典类型，抛出ValueError
        - 如果缺少必需字段且无法推断，抛出ValueError
        性能瓶颈：文件列表较长时的遍历验证
        排障入口：检查文件列表是否符合预期格式
        """
        if not files:
            return files

        for file in files:
            if not isinstance(file, dict):
                msg = "Files must be a list of dictionaries."
                raise ValueError(msg)  # noqa: TRY004

            if not all(key in file for key in ["path", "name", "type"]):
                # If any of the keys are missing, we should extract the
                # values from the file path
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
                    # get the file type from the path
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
        """从BaseMessage构建聊天输出响应。
        
        关键路径（三步）：
        1) 提取消息内容
        2) 使用提供的参数创建响应实例
        3) 返回构建的响应对象
        
        异常流：无显式异常处理
        性能瓶颈：消息内容的复制
        排障入口：检查返回的响应是否包含正确的消息和发送者信息
        """
        content = message.content
        return cls(message=content, sender=sender, sender_name=sender_name)

    @model_validator(mode="after")
    def validate_message(self):
        """验证消息内容。
        
        关键路径（三步）：
        1) 检查发送者是否为AI
        2) 如果是AI发送的消息，规范化换行符以符合markdown规范
        3) 替换单个换行为双换行，确保markdown渲染正确
        
        异常流：无显式异常处理
        性能瓶颈：字符串替换操作
        排障入口：检查消息内容的换行符是否已正确规范化
        """
        # The idea here is ensure the \n in message
        # is compliant with markdown if sender is machine
        # so, for example:
        # \n\n -> \n\n
        # \n -> \n\n

        if self.sender != MESSAGE_SENDER_AI:
            return self

        # We need to make sure we don't duplicate \n
        # in the message
        message = self.message.replace("\n\n", "\n")
        self.message = message.replace("\n", "\n\n")
        return self


class DataOutputResponse(BaseModel):
    """数据输出响应模式。
    
    用于定义数据输出响应的标准结构，包含一系列字典或None值。
    """

    data: list[dict | None]  # 数据列表，每个元素可以是字典或None


class ContainsEnumMeta(enum.EnumMeta):
    """支持'in'操作符的枚举元类。
    
    为枚举类添加'__contains__'方法，使其支持'item in enum'语法。
    """
    def __contains__(cls, item) -> bool:
        """检查项目是否为枚举的有效成员。
        
        关键路径（三步）：
        1) 尝试将项目转换为枚举成员
        2) 如果转换成功则返回True
        3) 如果抛出ValueError则返回False
        
        异常流：捕获ValueError并返回False
        性能瓶颈：枚举值的验证
        排障入口：检查项目是否为枚举的有效成员
        """
        try:
            cls(item)
        except ValueError:
            return False
        else:
            return True
