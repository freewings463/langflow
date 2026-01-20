"""
模块名称：文件夹接口兼容层

本模块仅保留历史 `/folders` 路由并全部重定向至 `/projects`。
主要功能：对旧接口进行临时重定向，保持兼容性。
设计背景：项目与文件夹概念合并后，保留旧入口避免客户端破坏性变更。
注意事项：所有接口返回 307 临时重定向。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from fastapi.responses import RedirectResponse
from fastapi_pagination import Params

from langflow.api.utils import custom_params
from langflow.services.database.models.flow.model import FlowRead
from langflow.services.database.models.folder.model import (
    FolderRead,
    FolderReadWithFlows,
)
from langflow.services.database.models.folder.pagination_model import FolderWithPaginatedFlows

router = APIRouter(prefix="/folders", tags=["Folders"])

# 迁移上下文：`/folders` 已合并到 `/projects`，此处仅保留重定向。


@router.post("/", response_model=FolderRead, status_code=201)
async def create_folder_redirect():
    """重定向到 `/projects`。"""
    return RedirectResponse(url="/api/v1/projects/", status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.get("/", response_model=list[FolderRead], status_code=200)
async def read_folders_redirect():
    """重定向到 `/projects`。"""
    return RedirectResponse(url="/api/v1/projects/", status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.get("/{folder_id}", response_model=FolderWithPaginatedFlows | FolderReadWithFlows, status_code=200)
async def read_folder_redirect(
    *,
    folder_id: UUID,
    params: Annotated[Params | None, Depends(custom_params)],
    is_component: bool = False,
    is_flow: bool = False,
    search: str = "",
):
    """重定向到 `/projects/{folder_id}`。"""
    redirect_url = f"/api/v1/projects/{folder_id}"
    params_list = []
    if is_component:
        params_list.append(f"is_component={is_component}")
    if is_flow:
        params_list.append(f"is_flow={is_flow}")
    if search:
        params_list.append(f"search={search}")
    if params and params.page:
        params_list.append(f"page={params.page}")
    if params and params.size:
        params_list.append(f"size={params.size}")

    if params_list:
        redirect_url += "?" + "&".join(params_list)

    return RedirectResponse(url=redirect_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.patch("/{folder_id}", response_model=FolderRead, status_code=200)
async def update_folder_redirect(
    *,
    folder_id: UUID,
):
    """重定向到 `/projects/{folder_id}`。"""
    return RedirectResponse(url=f"/api/v1/projects/{folder_id}", status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.delete("/{folder_id}", status_code=204)
async def delete_folder_redirect(
    *,
    folder_id: UUID,
):
    """重定向到 `/projects/{folder_id}`。"""
    return RedirectResponse(url=f"/api/v1/projects/{folder_id}", status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.get("/download/{folder_id}", status_code=200)
async def download_file_redirect(
    *,
    folder_id: UUID,
):
    """重定向到 `/projects/download/{folder_id}`。"""
    return RedirectResponse(
        url=f"/api/v1/projects/download/{folder_id}", status_code=status.HTTP_307_TEMPORARY_REDIRECT
    )


@router.post("/upload/", response_model=list[FlowRead], status_code=201)
async def upload_file_redirect():
    """重定向到 `/projects/upload/`。"""
    return RedirectResponse(url="/api/v1/projects/upload/", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
