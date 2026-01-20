"""
模块名称：`API` 数据模型

本模块定义 `API` 层通用返回结构，主要用于上传接口响应。主要功能包括：
- 规范文件上传返回字段与类型

关键组件：
- UploadFileResponse：上传返回模型

设计背景：统一前后端字段命名，减少解析分歧。
注意事项：`path` 为服务器侧存储路径，非客户端可直接访问的 URL。
"""

from pathlib import Path
from uuid import UUID

from pydantic import BaseModel

class UploadFileResponse(BaseModel):
    """文件上传响应模型。

    契约：`id` 为服务端生成；`path` 为服务器内部存储路径；`provider` 可为空。
    失败语义：模型校验失败会触发 `ValidationError`，由调用方处理。
    """

    id: UUID
    name: str
    path: Path
    size: int
    provider: str | None = None
