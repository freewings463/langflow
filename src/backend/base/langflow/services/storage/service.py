"""模块名称：存储服务抽象基类

模块目的：定义存储服务的统一接口与生命周期约定。
主要功能：约束文件保存、读取、列表与删除等核心能力。
使用场景：本地存储与对象存储实现的共同基类。
关键组件：`StorageService`
设计背景：为多种存储后端提供一致调用接口。
注意事项：子类需实现全部抽象方法并保证异常语义一致。
"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING

import anyio

from langflow.services.base import Service

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from langflow.services.session.service import SessionService
    from langflow.services.settings.service import SettingsService


class StorageService(Service):
    """存储服务抽象基类。"""

    name = "storage_service"

    def __init__(self, session_service: SessionService, settings_service: SettingsService):
        """初始化存储服务基类。

        契约：`config_dir` 必须可转换为 `anyio.Path`。
        副作用：设置 `data_dir` 并标记服务就绪。
        """
        self.settings_service = settings_service
        self.session_service = session_service
        self.data_dir: anyio.Path = anyio.Path(settings_service.settings.config_dir)
        self.set_ready()

    @abstractmethod
    def build_full_path(self, flow_id: str, file_name: str) -> str:
        """构建存储后端的完整路径或对象键。"""
        raise NotImplementedError

    @abstractmethod
    def parse_file_path(self, full_path: str) -> tuple[str, str]:
        """解析完整路径并拆分 `flow_id` 与 `file_name`。

        契约：输入为 `build_full_path` 的输出格式。
        失败语义：路径格式非法时抛 `ValueError`。
        """
        raise NotImplementedError

    def set_ready(self) -> None:
        """标记服务就绪。"""
        self.ready = True

    @abstractmethod
    async def save_file(self, flow_id: str, file_name: str, data: bytes, *, append: bool = False) -> None:
        """保存文件到存储后端。"""
        raise NotImplementedError

    @abstractmethod
    async def get_file(self, flow_id: str, file_name: str) -> bytes:
        """读取文件并返回字节内容。"""
        raise NotImplementedError

    @abstractmethod
    def get_file_stream(self, flow_id: str, file_name: str, chunk_size: int = 8192) -> AsyncIterator[bytes]:
        """以流式分块方式读取文件。

        契约：`chunk_size` 为单块字节数，调用方负责消费迭代器。
        失败语义：文件不存在时抛 `FileNotFoundError`。
        """
        raise NotImplementedError

    @abstractmethod
    async def list_files(self, flow_id: str) -> list[str]:
        """列出指定 `flow_id` 下的文件名列表。"""
        raise NotImplementedError

    @abstractmethod
    async def get_file_size(self, flow_id: str, file_name: str):
        """获取文件大小（字节数）。"""
        raise NotImplementedError

    @abstractmethod
    async def delete_file(self, flow_id: str, file_name: str) -> None:
        """删除指定文件。"""
        raise NotImplementedError

    @abstractmethod
    async def teardown(self) -> None:
        """服务销毁时的清理入口。"""
        raise NotImplementedError
