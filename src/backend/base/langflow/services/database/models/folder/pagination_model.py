"""
模块名称：文件夹分页响应模型

本模块定义包含分页流程列表的文件夹响应模型。
主要功能包括：将 `FolderRead` 与分页 `Flow` 结果组合输出。

关键组件：`FolderWithPaginatedFlows`
设计背景：统一 API 返回结构以支持分页展示。
使用场景：文件夹详情接口与列表接口。
注意事项：分页类型由 `fastapi_pagination.Page` 提供。
"""

from fastapi_pagination import Page

from langflow.helpers.base_model import BaseModel
from langflow.services.database.models.flow.model import Flow
from langflow.services.database.models.folder.model import FolderRead


class FolderWithPaginatedFlows(BaseModel):
    """包含分页流程的文件夹响应模型。

    契约：
    - 输出字段：`folder` 与 `flows`。
    - 用途：在单次响应中返回文件夹与分页流程列表。
    - 失败语义：字段校验失败抛异常。

    决策：使用 `Page[Flow]` 表达分页结果。
    问题：流程列表需要统一分页语义。
    方案：依赖 `fastapi_pagination` 的 `Page` 类型。
    代价：对外接口依赖分页库结构。
    重评：当分页协议变更时同步调整模型。
    """
    folder: FolderRead
    flows: Page[Flow]
