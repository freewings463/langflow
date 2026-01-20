"""
模块名称：缓存工具函数

本模块提供缓存目录管理、文件缓存与构建状态更新的辅助工具。
主要功能：
- 管理缓存目录与缓存文件清理；
- 保存上传文件与二进制内容；
- 更新构建状态到缓存。

设计背景：缓存服务需复用文件系统与状态缓存逻辑。
注意事项：文件操作可能抛 OSError，需调用方做好异常处理。
"""

import base64
import contextlib
import hashlib
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import UploadFile
from platformdirs import user_cache_dir

if TYPE_CHECKING:
    from lfx.schema.schema import BuildStatus

CACHE: dict[str, Any] = {}

CACHE_DIR = user_cache_dir("langflow", "langflow")

PREFIX = "langflow_cache"


class CacheMiss:
    """缓存未命中标记类型。"""
    def __repr__(self) -> str:
        return "<CACHE_MISS>"

    def __bool__(self) -> bool:
        return False


def create_cache_folder(func):
    def wrapper(*args, **kwargs):
        # 注意：确保缓存目录存在。
        cache_path = Path(CACHE_DIR) / PREFIX

        # 注意：目录不存在则创建。
        cache_path.mkdir(parents=True, exist_ok=True)

        return func(*args, **kwargs)

    return wrapper


@create_cache_folder
def clear_old_cache_files(max_cache_size: int = 3) -> None:
    """清理过期缓存文件

    契约：保留最近 `max_cache_size` 个缓存文件。
    """
    cache_dir = Path(tempfile.gettempdir()) / PREFIX
    cache_files = list(cache_dir.glob("*.dill"))

    if len(cache_files) > max_cache_size:
        cache_files_sorted_by_mtime = sorted(cache_files, key=lambda x: x.stat().st_mtime, reverse=True)

        for cache_file in cache_files_sorted_by_mtime[max_cache_size:]:
            with contextlib.suppress(OSError):
                cache_file.unlink()


def filter_json(json_data):
    """过滤 JSON 中不需要的运行时字段。"""
    filtered_data = json_data.copy()

    # 注意：移除 viewport/chatHistory 等运行时字段。
    if "viewport" in filtered_data:
        del filtered_data["viewport"]
    if "chatHistory" in filtered_data:
        del filtered_data["chatHistory"]

    # 注意：清理节点位置与拖拽状态字段。
    if "nodes" in filtered_data:
        for node in filtered_data["nodes"]:
            if "position" in node:
                del node["position"]
            if "positionAbsolute" in node:
                del node["positionAbsolute"]
            if "selected" in node:
                del node["selected"]
            if "dragging" in node:
                del node["dragging"]

    return filtered_data


@create_cache_folder
def save_binary_file(content: str, file_name: str, accepted_types: list[str]) -> str:
    """保存二进制文件到缓存目录

    契约：返回保存后的文件路径；文件类型不匹配抛 `ValueError`。
    """
    if not any(file_name.endswith(suffix) for suffix in accepted_types):
        msg = f"File {file_name} is not accepted"
        raise ValueError(msg)

    # 注意：写入缓存目录下。
    cache_path = Path(CACHE_DIR) / PREFIX
    if not content:
        msg = "Please, reload the file in the loader."
        raise ValueError(msg)
    data = content.split(",")[1]
    decoded_bytes = base64.b64decode(data)

    # 注意：构建目标文件路径。
    file_path = cache_path / file_name

    # 实现：将二进制内容写入文件。
    file_path.write_bytes(decoded_bytes)

    return str(file_path)


@create_cache_folder
def save_uploaded_file(file: UploadFile, folder_name):
    """保存上传文件并使用内容哈希命名

    契约：返回保存后的文件路径。
    关键路径：1) 计算内容哈希 2) 以哈希命名保存。
    """
    cache_path = Path(CACHE_DIR)
    folder_path = cache_path / folder_name
    filename = file.filename
    file_extension = Path(filename).suffix if isinstance(filename, str | Path) else ""
    file_object = file.file

    # 注意：目录不存在则创建。
    if not folder_path.exists():
        folder_path.mkdir()

    # 注意：使用 SHA256 生成内容哈希。
    sha256_hash = hashlib.sha256()
    # 注意：读取前先重置文件指针。
    file_object.seek(0)
    # 注意：分块读取以节省内存。
    while chunk := file_object.read(8192):  # 注意：每次读取 8KB 以控制内存。
        sha256_hash.update(chunk)

    # 注意：使用哈希摘要作为文件名。
    hex_dig = sha256_hash.hexdigest()
    file_name = f"{hex_dig}{file_extension}"

    # 注意：保存前再次重置指针。
    file_object.seek(0)

    # 实现：以哈希名保存文件。
    file_path = folder_path / file_name

    with file_path.open("wb") as new_file:
        while chunk := file_object.read(8192):
            new_file.write(chunk)

    return file_path


def update_build_status(cache_service, flow_id: str, status: "BuildStatus") -> None:
    """更新缓存中的构建状态

    契约：flow_id 必须存在于缓存；否则抛 `ValueError`。
    """
    cached_flow = cache_service[flow_id]
    if cached_flow is None:
        msg = f"Flow {flow_id} not found in cache"
        raise ValueError(msg)
    cached_flow["status"] = status
    cache_service[flow_id] = cached_flow
    cached_flow["status"] = status
    cache_service[flow_id] = cached_flow


CACHE_MISS = CacheMiss()
