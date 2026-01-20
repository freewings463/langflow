"""
模块名称：缓存工具与文件处理

本模块提供缓存目录维护、文件保存与缓存状态更新的工具函数。主要功能包括：
- 创建缓存目录并清理历史缓存文件
- 过滤 `Flow` JSON 中的非稳定字段
- 保存二进制内容与上传文件
- 更新构建状态缓存

关键组件：
- `create_cache_folder`：缓存目录装饰器
- `save_binary_file`/`save_uploaded_file`：文件保存工具

设计背景：缓存目录与文件处理在多处复用，需要统一入口
注意事项：文件操作可能抛出 `OSError`；路径基于 `platformdirs` 的用户缓存目录
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
    from langflow.api.v1.schemas import BuildStatus

CACHE: dict[str, Any] = {}

CACHE_DIR = user_cache_dir("langflow", "langflow")

PREFIX = "langflow_cache"


def create_cache_folder(func):
    """确保缓存目录存在的装饰器。

    契约：包裹函数执行前创建缓存目录；不改变被包裹函数签名。
    失败语义：目录创建失败时抛 `OSError`。
    """

    def wrapper(*args, **kwargs):
        cache_path = Path(CACHE_DIR) / PREFIX

        cache_path.mkdir(parents=True, exist_ok=True)

        return func(*args, **kwargs)

    return wrapper


@create_cache_folder
def clear_old_cache_files(max_cache_size: int = 3) -> None:
    """清理旧的缓存文件，保留最新的若干个。

    契约：输入最大保留数量；删除超出数量的 `.dill` 文件。
    失败语义：删除失败时忽略 `OSError`。
    """
    cache_dir = Path(tempfile.gettempdir()) / PREFIX
    cache_files = list(cache_dir.glob("*.dill"))

    if len(cache_files) > max_cache_size:
        cache_files_sorted_by_mtime = sorted(cache_files, key=lambda x: x.stat().st_mtime, reverse=True)

        for cache_file in cache_files_sorted_by_mtime[max_cache_size:]:
            with contextlib.suppress(OSError):
                cache_file.unlink()


def filter_json(json_data):
    """过滤 `Flow` JSON 中的非稳定字段。

    契约：输入 JSON 字典；输出副本并移除 `viewport`/`chatHistory`/节点位置等字段。
    注意：仅剔除展示相关字段，不改变结构性数据。
    """
    filtered_data = json_data.copy()
    if "viewport" in filtered_data:
        del filtered_data["viewport"]
    if "chatHistory" in filtered_data:
        del filtered_data["chatHistory"]

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
    """保存 base64 二进制内容为文件。

    契约：输入 `content`/`file_name`/`accepted_types`；返回保存路径字符串。
    失败语义：文件类型不在白名单或内容为空时抛 `ValueError`。
    """
    if not any(file_name.endswith(suffix) for suffix in accepted_types):
        msg = f"File {file_name} is not accepted"
        raise ValueError(msg)

    cache_path = Path(CACHE_DIR) / PREFIX
    if not content:
        msg = "Please, reload the file in the loader."
        raise ValueError(msg)
    data = content.split(",")[1]
    decoded_bytes = base64.b64decode(data)

    file_path = cache_path / file_name

    file_path.write_bytes(decoded_bytes)

    return str(file_path)


@create_cache_folder
def save_uploaded_file(file: UploadFile, folder_name):
    """保存上传文件并使用内容哈希作为文件名。

    契约：输入上传文件与目标文件夹；返回保存后的文件路径。
    关键路径：计算 `sha256`，按分块读取并写入文件。
    失败语义：文件写入失败时抛 `OSError`。
    """
    cache_path = Path(CACHE_DIR)
    folder_path = cache_path / folder_name
    filename = file.filename
    file_extension = Path(filename).suffix if isinstance(filename, str | Path) else ""
    file_object = file.file

    if not folder_path.exists():
        folder_path.mkdir()

    sha256_hash = hashlib.sha256()
    file_object.seek(0)
    while chunk := file_object.read(8192):
        sha256_hash.update(chunk)

    hex_dig = sha256_hash.hexdigest()
    file_name = f"{hex_dig}{file_extension}"

    file_object.seek(0)

    file_path = folder_path / file_name

    with file_path.open("wb") as new_file:
        while chunk := file_object.read(8192):
            new_file.write(chunk)

    return file_path


def update_build_status(cache_service, flow_id: str, status: "BuildStatus") -> None:
    """更新构建状态缓存。

    契约：输入缓存服务、`flow_id` 与状态；原地更新缓存对象。
    失败语义：缓存中未找到 `flow_id` 时抛 `ValueError`。
    实现：当前逻辑会写回缓存两次以保持现状。
    """
    cached_flow = cache_service[flow_id]
    if cached_flow is None:
        msg = f"Flow {flow_id} not found in cache"
        raise ValueError(msg)
    cached_flow["status"] = status
    cache_service[flow_id] = cached_flow
    cached_flow["status"] = status
    cache_service[flow_id] = cached_flow
