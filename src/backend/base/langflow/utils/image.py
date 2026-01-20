"""
模块名称：image

本模块提供图像处理功能，主要用于将图像文件转换为base64编码或数据URL。
主要功能包括：
- 将图像文件转换为base64编码字符串
- 创建图像的数据URL
- 为图像内容创建多模态输入字典

设计背景：在Web应用中，经常需要将图像文件转换为可以在前端直接使用的格式
注意事项：使用lru_cache装饰器缓存函数结果以提高性能
"""

import base64
import mimetypes
from functools import lru_cache
from pathlib import Path


def convert_image_to_base64(image_path: str | Path) -> str:
    """将图像文件转换为base64编码字符串。
    
    关键路径（三步）：
    1) 验证图像路径是否有效（非空、存在、是文件）
    2) 以二进制模式打开图像文件
    3) 读取文件内容并进行base64编码
    
    异常流：
    - ValueError：如果图像路径为空或不是文件
    - FileNotFoundError：如果图像文件不存在
    - OSError：如果读取文件时出错
    性能瓶颈：大图像文件的读取和编码
    排障入口：检查返回的base64字符串是否有效，文件路径是否正确
    """
    if not image_path:
        msg = "Image path cannot be empty"
        raise ValueError(msg)

    image_path = Path(image_path)

    if not image_path.exists():
        msg = f"Image file not found: {image_path}"
        raise FileNotFoundError(msg)

    if not image_path.is_file():
        msg = f"Path is not a file: {image_path}"
        raise ValueError(msg)

    try:
        with image_path.open("rb") as image_file:
            return base64.b64encode(image_file.read()).decode("utf-8")
    except OSError as e:
        msg = f"Error reading image file: {e}"
        raise OSError(msg) from e


def create_data_url(image_path: str | Path, mime_type: str | None = None) -> str:
    """从图像文件创建数据URL。
    
    关键路径（三步）：
    1) 如果未指定MIME类型，则从文件扩展名推断
    2) 将图像转换为base64编码
    3) 组合MIME类型和base64数据创建数据URL
    
    异常流：
    - ValueError：如果无法确定MIME类型
    - FileNotFoundError：如果图像文件不存在
    - OSError：如果读取文件时出错
    性能瓶颈：图像文件的读取和编码
    排障入口：检查返回的数据URL格式是否正确
    """
    if not mime_type:
        mime_type = mimetypes.guess_type(str(image_path))[0]
        if not mime_type:
            msg = f"Could not determine MIME type for: {image_path}"
            raise ValueError(msg)

    try:
        base64_data = convert_image_to_base64(image_path)
    except (OSError, FileNotFoundError, ValueError) as e:
        msg = f"Failed to create data URL: {e}"
        raise type(e)(msg) from e
    return f"data:{mime_type};base64,{base64_data}"


@lru_cache(maxsize=50)
def create_image_content_dict(image_path: str | Path, mime_type: str | None = None) -> dict:
    """从图像文件创建多模态输入的内容字典。
    
    关键路径（三步）：
    1) 如果未指定MIME类型，则从文件扩展名推断
    2) 将图像转换为base64编码
    3) 创建符合多模态输入格式的字典结构
    
    异常流：
    - ValueError：如果无法确定MIME类型
    - FileNotFoundError：如果图像文件不存在
    - OSError：如果读取文件时出错
    性能瓶颈：图像文件的读取和编码，lru_cache提供缓存优化
    排障入口：检查返回的字典格式是否符合多模态输入要求
    """
    if not mime_type:
        mime_type = mimetypes.guess_type(str(image_path))[0]
        if not mime_type:
            msg = f"Could not determine MIME type for: {image_path}"
            raise ValueError(msg)

    try:
        base64_data = convert_image_to_base64(image_path)
    except (OSError, FileNotFoundError, ValueError) as e:
        msg = f"Failed to create image content dict: {e}"
        raise type(e)(msg) from e

    return {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64_data}"}}
