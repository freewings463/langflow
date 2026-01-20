"""图像相关工具与模型。"""

import base64
from pathlib import Path

import aiofiles
from PIL import Image as PILImage
from platformdirs import user_cache_dir
from pydantic import BaseModel

from lfx.services.deps import get_storage_service
from lfx.utils.image import create_image_content_dict

IMAGE_ENDPOINT = "/files/images/"


def is_image_file(file_path) -> bool:
    """检查文件是否为有效图像。

    契约：
    - 输入：文件路径
    - 输出：是否为有效图像
    - 副作用：无
    - 失败语义：文件无法打开或验证时返回 False
    """
    try:
        with PILImage.open(file_path) as img:
            img.verify()  # 校验是否为有效图像
    except (OSError, SyntaxError):
        return False
    return True


def get_file_paths(files: list[str | dict]):
    """获取文件路径列表。

    关键路径（三步）：
    1) 检查是否存在存储服务
    2) 处理文件列表，提取路径
    3) 返回处理后的路径列表

    异常流：无显式异常处理。
    性能瓶颈：文件路径解析。
    排障入口：无特定日志输出。
    """
    if not files:
        return []

    storage_service = get_storage_service()
    if not storage_service:
        # 从字典中提取路径

        extracted_files = []
        cache_dir = Path(user_cache_dir("langflow"))

        for file in files:
            if not file:  # 跳过空/None
                continue

            # 处理 Image/dict/字符串
            if isinstance(file, dict) and "path" in file:
                file_path = file["path"]
            elif hasattr(file, "path") and file.path:
                file_path = file.path
            else:
                file_path = file

            if not file_path:  # 跳过空路径
                continue

            # 若为相对路径则尝试解析到缓存目录
            path = Path(file_path)
            if not path.is_absolute() and not path.exists():
                # 检查缓存目录是否存在
                cache_path = cache_dir / file_path
                if cache_path.exists():
                    extracted_files.append(str(cache_path))
                else:
                    # 未找到则保留原路径
                    extracted_files.append(file_path)
            else:
                extracted_files.append(file_path)
        return extracted_files

    file_paths = []
    for file in files:
        # 处理 dict 类型
        if storage_service is None:
            continue

        if not file:  # 跳过空/None
            continue

        if isinstance(file, dict) and "path" in file:
            file_path_str = file["path"]
        elif hasattr(file, "path") and file.path:
            file_path_str = file.path
        else:
            file_path_str = file

        if not file_path_str:  # 跳过空路径
            continue

        flow_id, file_name = storage_service.parse_file_path(file_path_str)

        if not file_name:  # 跳过无文件名
            continue

        file_paths.append(storage_service.build_full_path(flow_id=flow_id, file_name=file_name))
    return file_paths


async def get_files(
    file_paths: list[str],
    *,
    convert_to_base64: bool = False,
):
    """从存储服务获取文件。

    关键路径（三步）：
    1) 检查是否存在存储服务
    2) 根据存储服务可用性读取文件
    3) 可选转换为 base64 格式

    异常流：文件不存在或读取失败时抛出 FileNotFoundError。
    性能瓶颈：文件读取和 base64 转换。
    排障入口：无特定日志输出。
    """
    if not file_paths:
        return []

    storage_service = get_storage_service()
    if not storage_service:
        # 无存储服务时直接读取本地文件（测试用途）
        file_objects: list[str | bytes] = []
        for file_path_str in file_paths:
            if not file_path_str:  # 跳过空路径
                continue

            file_path = Path(file_path_str)
            if file_path.exists():
                # 异步读取以保持兼容
                try:
                    async with aiofiles.open(file_path, "rb") as f:
                        file_content = await f.read()
                    if convert_to_base64:
                        file_base64 = base64.b64encode(file_content).decode("utf-8")
                        file_objects.append(file_base64)
                    else:
                        file_objects.append(file_content)
                except Exception as e:
                    msg = f"Error reading file {file_path}: {e}"
                    raise FileNotFoundError(msg) from e
            else:
                msg = f"File not found: {file_path}"
                raise FileNotFoundError(msg)
        return file_objects

    file_objects: list[str | bytes] = []
    for file in file_paths:
        if not file:  # 跳过空路径
            continue

        flow_id, file_name = storage_service.parse_file_path(file)

        if not file_name:  # 跳过无文件名
            continue

        if not storage_service:
            continue

        try:
            file_object = await storage_service.get_file(flow_id=flow_id, file_name=file_name)
            if convert_to_base64:
                file_base64 = base64.b64encode(file_object).decode("utf-8")
                file_objects.append(file_base64)
            else:
                file_objects.append(file_object)
        except Exception as e:
            msg = f"Error getting file {file} from storage: {e}"
            raise FileNotFoundError(msg) from e
    return file_objects


class Image(BaseModel):
    """图像模型。

    关键路径（三步）：
    1) 维护路径/URL 属性；
    2) 提供 base64 与内容字典转换；
    3) 生成可访问 URL。
    """

    path: str | None = None
    url: str | None = None

    def to_base64(self):
        """将图像转换为 base64 字符串。"""
        if self.path:
            files = get_files([self.path], convert_to_base64=True)
            if not files:
                msg = f"No files found or file could not be converted to base64: {self.path}"
                raise ValueError(msg)
            return files[0]
        msg = "Image path is not set."
        raise ValueError(msg)

    def to_content_dict(self, flow_id: str | None = None):
        """转换为内容字典（可选拼接 flow_id）。"""
        if not self.path:
            msg = "Image path is not set."
            raise ValueError(msg)

        # 若路径不含 "/" 且提供 flow_id，则前置 flow_id
        image_path = self.path
        if flow_id and "/" not in self.path:
            image_path = f"{flow_id}/{self.path}"

        # 使用工具函数生成内容字典
        return create_image_content_dict(image_path, None, None)

    def get_url(self) -> str:
        """获取图像 URL。"""
        return f"{IMAGE_ENDPOINT}{self.path}"
