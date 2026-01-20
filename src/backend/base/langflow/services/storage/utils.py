"""模块名称：存储工具函数

模块目的：提供存储相关的通用工具导出。
主要功能：暴露扩展名到 `Content-Type` 的映射函数。
使用场景：文件上传或下载时设置正确的 `Content-Type`。
关键组件：`build_content_type_from_extension`
设计背景：避免重复实现 MIME 推断逻辑。
注意事项：仅做导出，不包含额外逻辑。
"""

from lfx.utils.helpers import build_content_type_from_extension

__all__ = [
    "build_content_type_from_extension",
]
