"""模块名称：本地文件存储服务

模块目的：提供基于本地文件系统的存储实现。
主要功能：文件保存、读取、流式读取、列举与删除。
使用场景：默认本地存储或不启用对象存储时使用。
关键组件：`LocalStorageService`
设计背景：为存储接口提供本地实现，便于开发与部署。
注意事项：路径以 `flow_id/filename` 组织，异常由文件系统抛出。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from aiofile import async_open

from langflow.logging.logger import logger
from langflow.services.storage.service import StorageService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from langflow.services.session.service import SessionService
    from langflow.services.settings.service import SettingsService

# 路径解析常量：路径格式为 `flow_id/filename`
EXPECTED_PATH_PARTS = 2


class LocalStorageService(StorageService):
    """本地文件存储服务。"""

    def __init__(
        self,
        session_service: SessionService,
        settings_service: SettingsService,
    ) -> None:
        """初始化本地存储服务。

        契约：`config_dir` 必须可用作本地存储根目录。
        副作用：初始化基类并设置 `data_dir`。
        """
        super().__init__(session_service, settings_service)

    def resolve_component_path(self, logical_path: str) -> str:
        """将逻辑路径转换为本地绝对路径。

        契约：期望格式为 `flow_id/filename`，否则返回原始路径。
        """
        parts = logical_path.split("/", 1)
        if len(parts) != EXPECTED_PATH_PARTS:
            return logical_path

        flow_id, file_name = parts
        return self.build_full_path(flow_id, file_name)

    def build_full_path(self, flow_id: str, file_name: str) -> str:
        """构建本地存储中文件的完整路径。"""
        return str(self.data_dir / flow_id / file_name)

    def parse_file_path(self, full_path: str) -> tuple[str, str]:
        r"""解析本地路径并提取 `flow_id` 与 `file_name`。

        关键路径（三步）：
        1) 使用 `Path` 标准化路径（兼容 `Windows` 反斜杠）
        2) 去除 `data_dir` 前缀（如存在）
        3) 以最后一个 `/` 拆分为 `flow_id` 与文件名

        异常流：无显式异常；无法拆分时返回 `("", file_name)`。
        性能瓶颈：路径字符串规范化开销。
        排障入口：无日志，需由调用方校验返回值。
        """
        full_path_obj = Path(full_path)
        data_dir_path = Path(self.data_dir)

        try:
            path_without_prefix = full_path_obj.relative_to(data_dir_path)
        except ValueError:
            path_without_prefix = full_path_obj

        path_str = str(path_without_prefix).replace("\\", "/")

        if "/" not in path_str:
            return "", path_str

        flow_id, file_name = path_str.rsplit("/", 1)
        return flow_id, file_name

    async def save_file(self, flow_id: str, file_name: str, data: bytes, *, append: bool = False) -> None:
        """保存文件到本地存储。

        关键路径（三步）：
        1) 确保 `flow_id` 目录存在
        2) 以追加或覆盖模式写入字节数据
        3) 记录成功/失败日志

        异常流：写入失败时抛出底层异常并记录日志。
        性能瓶颈：磁盘写入与目录创建。
        排障入口：日志关键字 `Error saving file`。
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
        """读取本地文件并返回字节内容。

        关键路径（三步）：
        1) 校验文件是否存在
        2) 以二进制模式读取内容
        3) 返回内容并记录日志

        异常流：文件不存在时抛 `FileNotFoundError`。
        性能瓶颈：磁盘读取。
        排障入口：日志关键字 `File {file_name} not found`。
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

    async def get_file_stream(self, flow_id: str, file_name: str, chunk_size: int = 8192) -> AsyncIterator[bytes]:
        """以流式分块读取文件内容。

        关键路径（三步）：
        1) 校验文件存在
        2) 逐块读取并 `yield`
        3) 读取结束后正常退出

        异常流：文件不存在时抛 `FileNotFoundError`。
        性能瓶颈：磁盘顺序读取。
        排障入口：日志关键字 `File {file_name} not found`。
        """
        file_path = self.data_dir / flow_id / file_name
        if not await file_path.exists():
            await logger.awarning(f"File {file_name} not found in flow {flow_id}.")
            msg = f"File {file_name} not found in flow {flow_id}"
            raise FileNotFoundError(msg)

        async with async_open(str(file_path), "rb") as f:
            while True:
                chunk = await f.read(chunk_size)
                if not chunk:
                    break
                yield chunk

    async def list_files(self, flow_id: str) -> list[str]:
        """列出指定 `flow_id` 目录下的文件名。

        关键路径（三步）：
        1) 校验目录存在与类型
        2) 异步遍历文件项
        3) 返回文件名列表

        异常流：遍历异常时记录日志并返回空列表。
        性能瓶颈：目录遍历 `iterdir`。
        排障入口：日志关键字 `Error listing files`。
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
        """删除本地文件。"""
        file_path = self.data_dir / flow_id / file_name
        if await file_path.exists():
            await file_path.unlink()
            await logger.ainfo(f"File {file_name} deleted successfully from flow {flow_id}.")
        else:
            await logger.awarning(f"Attempted to delete non-existent file {file_name} in flow {flow_id}.")

    async def get_file_size(self, flow_id: str, file_name: str) -> int:
        """获取文件大小（字节数）。

        关键路径（三步）：
        1) 校验文件存在
        2) 读取 `stat` 信息
        3) 返回文件大小

        异常流：文件不存在时抛 `FileNotFoundError`。
        性能瓶颈：`stat` 调用。
        排障入口：日志关键字 `Error getting size of file`。
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

    async def teardown(self) -> None:
        """服务销毁时的清理入口（本地存储无需额外处理）。"""
