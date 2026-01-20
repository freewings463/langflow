"""模块名称：文件与 MIME 辅助工具

模块目的：统一文件扩展名到 MIME/Content-Type 的推断逻辑。
主要功能：
- 基于扩展名推断 MIME
- 扩展名到 Content-Type 的快速映射
使用场景：文件上传、数据 URL 构建、HTTP 响应头设置。
关键组件：`get_mime_type`、`build_content_type_from_extension`
设计背景：多处需要一致的 MIME 规则，避免分散硬编码。
注意事项：`get_mime_type` 解析失败会抛 `ValueError`。
"""

from __future__ import annotations

import mimetypes
from typing import TYPE_CHECKING

from lfx.utils.constants import EXTENSION_TO_CONTENT_TYPE

if TYPE_CHECKING:
    from pathlib import Path


def get_mime_type(file_path: str | Path) -> str:
    """根据扩展名推断文件 MIME 类型。

    契约：使用 `mimetypes.guess_type`，失败时抛 `ValueError`。
    """
    mime_type, _ = mimetypes.guess_type(str(file_path))
    if mime_type is None:
        msg = f"Could not determine MIME type for: {file_path}"
        raise ValueError(msg)
    return mime_type


def build_content_type_from_extension(extension: str):
    """将扩展名映射为 Content-Type，缺省回退为 `application/octet-stream`。"""
    return EXTENSION_TO_CONTENT_TYPE.get(extension.lower(), "application/octet-stream")
