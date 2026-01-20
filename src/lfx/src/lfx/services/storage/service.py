from __future__ import annotations

"""
模块名称：存储服务抽象层

本模块定义存储服务的抽象接口，约束不同后端的文件操作行为。
主要功能包括：
- 定义文件读写/删除/列举的统一接口
- 约定逻辑路径与命名空间规则
- 提供默认的文件流式读取实现

关键组件：
- `StorageService`
- `get_file_stream`

设计背景：便于替换存储后端（本地/S3 等）而不影响上层组件。
注意事项：所有文件操作以 `flow_id` 作为命名空间隔离。
"""

from abc import abstractmethod
from typing import TYPE_CHECKING

import anyio

from lfx.services.base import Service

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from lfx.services.settings.service import SettingsService


class StorageService(Service):
    """存储服务抽象基类。

    契约：
    - 输入：`flow_id`/`file_name` 组合的逻辑路径
    - 输出：文件字节内容或路径字符串
    - 副作用：由具体后端实现决定
    - 失败语义：子类应抛出明确的文件错误或传递底层异常
    """

    name = "storage_service"

    def __init__(self, session_service, settings_service: SettingsService):
        """初始化存储服务基类。"""
        self.settings_service = settings_service
        self.session_service = session_service
        self.data_dir: anyio.Path = anyio.Path(settings_service.settings.config_dir)
        self.set_ready()

    @abstractmethod
    def build_full_path(self, flow_id: str, file_name: str) -> str:
        """构建文件完整路径或存储键。"""
        raise NotImplementedError

    @abstractmethod
    def parse_file_path(self, full_path: str) -> tuple[str, str]:
        """解析完整路径为 (flow_id, file_name)。"""
        raise NotImplementedError

    @abstractmethod
    def resolve_component_path(self, logical_path: str) -> str:
        """将逻辑路径转换为组件可直接使用的路径。"""
        raise NotImplementedError

    def set_ready(self) -> None:
        """标记服务已就绪。"""
        self._ready = True

    @abstractmethod
    async def save_file(self, flow_id: str, file_name: str, data: bytes, *, append: bool = False) -> None:
        """保存文件。"""
        raise NotImplementedError

    @abstractmethod
    async def get_file(self, flow_id: str, file_name: str) -> bytes:
        """读取文件内容。"""
        raise NotImplementedError

    async def get_file_stream(self, flow_id: str, file_name: str, chunk_size: int = 8192) -> AsyncIterator[bytes]:
        """以流方式读取文件（默认分块输出）。"""
        # 注意：默认实现一次性读取后再分块
        content = await self.get_file(flow_id, file_name)
        for i in range(0, len(content), chunk_size):
            yield content[i : i + chunk_size]

    @abstractmethod
    async def list_files(self, flow_id: str) -> list[str]:
        """列举某个命名空间下的文件名列表。"""
        raise NotImplementedError

    @abstractmethod
    async def get_file_size(self, flow_id: str, file_name: str) -> int:
        """获取文件大小（字节）。"""
        raise NotImplementedError

    @abstractmethod
    async def delete_file(self, flow_id: str, file_name: str) -> None:
        """删除文件。"""
        raise NotImplementedError

    async def teardown(self) -> None:
        """服务关闭时执行清理（由子类实现）。"""
        raise NotImplementedError
