"""模块名称：图像与数据 URL 工具

模块目的：统一图像读取、编码与多模态输入格式。
主要功能：
- 兼容本地文件与存储服务读取
- 生成多模态模型通用的 `image_url` 内容结构
使用场景：多模态模型输入、前端预览与数据 URL 构建。
关键组件：`convert_image_to_base64`、`create_data_url`、`create_image_content_dict`
设计背景：多模型输入需要统一图像格式，并兼容远端存储。
注意事项：存储服务失败会记录日志并抛出原异常。
"""

from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path

from lfx.log import logger
from lfx.services.deps import get_storage_service
from lfx.utils.async_helpers import run_until_complete
from lfx.utils.helpers import get_mime_type


def convert_image_to_base64(image_path: str | Path) -> str:
    """将图像文件编码为 Base64 字符串。

    关键路径：
    1) 若配置存储服务则优先读取远端文件
    2) 远端读取失败记录日志并抛出
    3) 回退本地文件读取并编码

    契约：优先使用存储服务读取；失败或未配置时回退本地文件读取。
    失败语义：本地文件不存在抛 `FileNotFoundError`。
    """
    image_path = Path(image_path)

    storage_service = get_storage_service()
    if storage_service:
        flow_id, file_name = storage_service.parse_file_path(str(image_path))
        try:
            file_content = run_until_complete(
                storage_service.get_file(flow_id=flow_id, file_name=file_name)  # type: ignore[call-arg]
            )
            return base64.b64encode(file_content).decode("utf-8")
        except Exception as e:
            # 排障：存储读取失败将记录错误并向上抛出，调用方需捕获。
            logger.error(f"Error reading image file: {e}")
            raise

    # 回退到本地文件读取
    if not image_path.exists():
        msg = f"Image file not found: {image_path}"
        raise FileNotFoundError(msg)

    with image_path.open("rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")


def create_data_url(image_path: str | Path, mime_type: str | None = None) -> str:
    """将图像编码为 `data:` URL。

    契约：`mime_type=None` 时自动推断；读取失败由下游函数抛出。
    """
    image_path = Path(image_path)

    if mime_type is None:
        mime_type = get_mime_type(image_path)

    base64_data = convert_image_to_base64(image_path)
    return f"data:{mime_type};base64,{base64_data}"


@lru_cache(maxsize=50)
def create_image_content_dict(
    image_path: str | Path,
    mime_type: str | None = None,
    model_name: str | None = None,  # noqa: ARG001
) -> dict:
    """构建多模态 `image_url` 内容字典。

    契约：结果结构适配主流模型 `{"type": "image_url", "image_url": {"url": ...}}`。
    性能：使用 LRU 缓存（最多 50 个）减少重复编码。
    """
    data_url = create_data_url(image_path, mime_type)

    # 注意：保持跨模型统一格式，避免提供方差异。
    return {"type": "image_url", "image_url": {"url": data_url}}
