"""
模块名称：本地文件存储服务

本模块实现基于本地文件系统的存储服务，提供文件保存、读取、列举与删除能力。
主要功能包括：
- 将逻辑路径解析为本地绝对路径
- 保存/读取/删除文件并记录日志
- 列举文件与获取文件大小

关键组件：
- `LocalStorageService`
- `resolve_component_path`
- `save_file` / `get_file` / `list_files`

设计背景：为单机部署提供默认的文件存储实现。
注意事项：路径格式要求 `flow_id/filename`，不符合将原样返回。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiofile import async_open

from lfx.log.logger import logger
from lfx.services.base import Service
from lfx.services.storage.service import StorageService

if TYPE_CHECKING:
    from langflow.services.session.service import SessionService

    from lfx.services.settings.service import SettingsService

# 注意：路径格式为 "flow_id/filename"
EXPECTED_PATH_PARTS = 2


class LocalStorageService(StorageService, Service):
    """本地文件存储服务。

    契约：
    - 输入：`flow_id` 与 `file_name` 组合成的逻辑路径
    - 输出：本地文件操作结果
    - 副作用：读写本地文件系统并记录日志
    - 失败语义：文件不存在抛 `FileNotFoundError`，写入失败抛原始异常
    """

    def __init__(
        self,
        session_service: SessionService,
        settings_service: SettingsService,
    ) -> None:
        """初始化本地存储服务。"""
        # 注意：基类负责初始化 data_dir 与 ready 状态
        super().__init__(session_service, settings_service)

    def resolve_component_path(self, logical_path: str) -> str:
        """将逻辑路径转换为本地绝对路径。

        契约：
        - 输入：`flow_id/filename`
        - 输出：绝对路径字符串
        - 副作用：无
        - 失败语义：格式不符合时返回原始路径
        """
        # 解析逻辑路径为 flow_id 与文件名
        parts = logical_path.split("/", 1)
        if len(parts) != EXPECTED_PATH_PARTS:
            # 注意：格式异常时直接返回原始值
            return logical_path

        flow_id, file_name = parts
        return self.build_full_path(flow_id, file_name)

    async def teardown(self) -> None:
        """关闭服务（本地存储无额外清理）。"""
        # 注意：本地存储无资源释放需求

    def build_full_path(self, flow_id: str, file_name: str) -> str:
        """构建本地文件完整路径。"""
        return str(self.data_dir / flow_id / file_name)

    def parse_file_path(self, full_path: str) -> tuple[str, str]:
        r"""Parse a full local storage path to extract flow_id and file_name.

        Args:
            full_path: Filesystem path, may or may not include data_dir
                e.g., "/data/user_123/image.png" or "user_123/image.png". On Windows the
                separators may be backslashes ("\\"). This method handles both.

        Returns:
            tuple[str, str]: A tuple of (flow_id, file_name)

        Examples:
            >>> parse_file_path("/data/user_123/image.png")  # 注意：包含 data_dir 前缀
            ("user_123", "image.png")
            >>> parse_file_path("user_123/image.png")  # 注意：不包含 data_dir 前缀
            ("user_123", "image.png")
        """
        """从完整路径解析 flow_id 与文件名。

        契约：
        - 输入：绝对或相对路径
        - 输出：`(flow_id, file_name)`
        - 副作用：无
        - 失败语义：无分隔符时返回空 flow_id
        """
        data_dir_str = str(self.data_dir)

        # 注意：若包含 data_dir 前缀则剥离
        path_without_prefix = full_path
        if full_path.startswith(data_dir_str):
            # 同时剥离 POSIX/Windows 分隔符
            path_without_prefix = full_path[len(data_dir_str) :].lstrip("/").lstrip("\\")

        # 统一分隔符为 POSIX 形式，便于后续解析
        normalized_path = path_without_prefix.replace("\\", "/")

        # 右侧分割：最后一段为文件名，其余为 flow_id
        if "/" not in normalized_path:
            return "", normalized_path

        # 仅切分一次
        flow_id, file_name = normalized_path.rsplit("/", 1)
        return flow_id, file_name

    async def save_file(self, flow_id: str, file_name: str, data: bytes, *, append: bool = False) -> None:
        """保存文件到本地存储。

        契约：
        - 输入：`flow_id`/`file_name`/字节内容
        - 输出：无
        - 副作用：创建目录并写入文件
        - 失败语义：写入失败抛异常
        """
        folder_path = self.data_dir / flow_id
        await folder_path.mkdir(parents=True, exist_ok=True)
        file_path = folder_path / file_name

        try:
            mode = "ab" if append else "wb"
            async with async_open(str(file_path), mode) as f:
                await f.write(data)
            action = "appended to" if append else "saved"
            await logger.ainfo(f"File {file_name} {action} successfully in flow {flow_id}.")
        except Exception:
            logger.exception(f"Error saving file {file_name} in flow {flow_id}")
            raise

    async def get_file(self, flow_id: str, file_name: str) -> bytes:
        """读取本地文件内容。

        契约：
        - 输入：`flow_id`/`file_name`
        - 输出：文件字节内容
        - 副作用：无
        - 失败语义：文件不存在抛 `FileNotFoundError`
        """
        file_path = self.data_dir / flow_id / file_name
        if not await file_path.exists():
            await logger.awarning(f"File {file_name} not found in flow {flow_id}.")
            msg = f"File {file_name} not found in flow {flow_id}"
            raise FileNotFoundError(msg)

        async with async_open(str(file_path), "rb") as f:
            content = await f.read()

        logger.debug(f"File {file_name} retrieved successfully from flow {flow_id}.")
        return content

    async def list_files(self, flow_id: str) -> list[str]:
        """列举某个 flow 目录下的文件名列表。

        契约：
        - 输入：`flow_id`
        - 输出：文件名列表（可能为空）
        - 副作用：读取目录并记录日志
        - 失败语义：目录不存在返回空列表
        """
        if not isinstance(flow_id, str):
            flow_id = str(flow_id)

        folder_path = self.data_dir / flow_id
        if not await folder_path.exists() or not await folder_path.is_dir():
            await logger.awarning(f"Flow {flow_id} directory does not exist.")
            return []

        try:
            files = [p.name async for p in folder_path.iterdir() if await p.is_file()]
        except Exception:  # noqa: BLE001
            logger.exception(f"Error listing files in flow {flow_id}")
            return []
        else:
            await logger.ainfo(f"Listed {len(files)} files in flow {flow_id}.")
            return files

    async def delete_file(self, flow_id: str, file_name: str) -> None:
        """删除本地存储文件。

        契约：
        - 输入：`flow_id`/`file_name`
        - 输出：无
        - 副作用：删除文件并记录日志
        - 失败语义：文件不存在时不抛异常
        """
        file_path = self.data_dir / flow_id / file_name
        if await file_path.exists():
            await file_path.unlink()
            await logger.ainfo(f"File {file_name} deleted successfully from flow {flow_id}.")
        else:
            await logger.awarning(f"Attempted to delete non-existent file {file_name} in flow {flow_id}.")

    async def get_file_size(self, flow_id: str, file_name: str) -> int:
        """获取文件大小（字节）。

        契约：
        - 输入：`flow_id`/`file_name`
        - 输出：文件大小（字节）
        - 副作用：读取文件元数据
        - 失败语义：文件不存在抛 `FileNotFoundError`
        """
        file_path = self.data_dir / flow_id / file_name
        if not await file_path.exists():
            await logger.awarning(f"File {file_name} not found in flow {flow_id}.")
            msg = f"File {file_name} not found in flow {flow_id}"
            raise FileNotFoundError(msg)

        try:
            file_size_stat = await file_path.stat()
        except Exception:
            logger.exception(f"Error getting size of file {file_name} in flow {flow_id}")
            raise
        else:
            return file_size_stat.st_size
