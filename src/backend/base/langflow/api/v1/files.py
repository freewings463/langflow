"""
模块名称：文件与头像资源接口

本模块提供流程文件上传/下载、图片访问与头像目录读取等能力。
主要功能：
- 按流程存储与检索文件
- 下载图片与头像资源
- 列出可用的头像文件
设计背景：统一文件存储接口并提供安全的本地文件访问。
注意事项：对路径做严格校验以防目录穿越；异常统一转为 4xx/5xx。
"""

import hashlib
from datetime import datetime, timezone
from http import HTTPStatus
from io import BytesIO
from pathlib import Path
from typing import Annotated
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from lfx.services.settings.service import SettingsService
from lfx.utils.helpers import build_content_type_from_extension

from langflow.api.utils import CurrentActiveUser, DbSession, ValidatedFileName
from langflow.api.v1.schemas import UploadFileResponse
from langflow.services.database.models.flow.model import Flow
from langflow.services.deps import get_settings_service, get_storage_service
from langflow.services.storage.service import StorageService

router = APIRouter(tags=["Files"], prefix="/files")


def _get_allowed_profile_picture_folders(settings_service: SettingsService) -> set[str]:
    """返回允许访问的头像目录集合。

    契约：
    - 输入：`settings_service`
    - 输出：允许的目录名集合
    - 失败语义：读取失败时回退到内置默认目录
    """
    allowed: set[str] = set()
    try:
        # 实现：优先读取用户配置目录的头像文件夹。
        config_dir = Path(settings_service.settings.config_dir)
        cfg_base = config_dir / "profile_pictures"
        if cfg_base.exists():
            allowed.update({p.name for p in cfg_base.iterdir() if p.is_dir()})
        # 实现：补充包内置头像目录。
        from langflow.initial_setup import setup

        pkg_base = Path(setup.__file__).parent / "profile_pictures"
        if pkg_base.exists():
            allowed.update({p.name for p in pkg_base.iterdir() if p.is_dir()})
    except Exception as _:
        import logging

        logger = logging.getLogger(__name__)
        logger.exception("Exception occurred while getting allowed profile picture folders")

    # 注意：默认目录保证开箱即用与测试稳定。
    return allowed or {"People", "Space"}


# 实现：依赖项根据 `flow_id` 读取流程并进行权限校验。
async def get_flow(
    flow_id: UUID,
    current_user: CurrentActiveUser,
    session: DbSession,
):
    """读取流程并校验所有权。

    失败语义：未找到或无权限统一返回 404。
    """
    # 注意：`session.get` 可避免 `SelectOfScalar` 的 `.first()` 兼容问题。
    flow = await session.get(Flow, flow_id)
    # 安全：无权限与不存在统一 404，避免泄露信息。
    if not flow or flow.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Flow not found")
    return flow


@router.post("/upload/{flow_id}", status_code=HTTPStatus.CREATED)
async def upload_file(
    *,
    file: UploadFile,
    flow: Annotated[Flow, Depends(get_flow)],
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
) -> UploadFileResponse:
    """上传文件并绑定到流程。

    契约：
    - 输入：`UploadFile` 与 `flow`
    - 输出：`UploadFileResponse`
    - 失败语义：超出大小限制返回 413，其余异常返回 500
    """
    try:
        max_file_size_upload = settings_service.settings.max_file_size_upload
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    if file.size > max_file_size_upload * 1024 * 1024:
        raise HTTPException(
            status_code=413, detail=f"File size is larger than the maximum file size {max_file_size_upload}MB."
        )

    # 注意：权限校验由 `get_flow` 依赖完成。
    try:
        file_content = await file.read()
        timestamp = datetime.now(tz=timezone.utc).astimezone().strftime("%Y-%m-%d_%H-%M-%S")
        file_name = file.filename or hashlib.sha256(file_content).hexdigest()
        full_file_name = f"{timestamp}_{file_name}"
        folder = str(flow.id)
        await storage_service.save_file(flow_id=folder, file_name=full_file_name, data=file_content)
        return UploadFileResponse(flow_id=str(flow.id), file_path=f"{folder}/{full_file_name}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/download/{flow_id}/{file_name}")
async def download_file(
    file_name: ValidatedFileName,
    flow: Annotated[Flow, Depends(get_flow)],
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
):
    """下载流程文件。"""
    # 注意：权限校验由 `get_flow` 依赖完成。
    flow_id_str = str(flow.id)
    extension = file_name.split(".")[-1]

    if not extension:
        raise HTTPException(status_code=500, detail=f"Extension not found for file {file_name}")
    try:
        content_type = build_content_type_from_extension(extension)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    if not content_type:
        raise HTTPException(status_code=500, detail=f"Content type not found for extension {extension}")

    try:
        file_content = await storage_service.get_file(flow_id=flow_id_str, file_name=file_name)
        headers = {
            "Content-Disposition": f"attachment; filename={file_name} filename*=UTF-8''{file_name}",
            "Content-Type": "application/octet-stream",
            "Content-Length": str(len(file_content)),
        }
        return StreamingResponse(BytesIO(file_content), media_type=content_type, headers=headers)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/images/{flow_id}/{file_name}")
async def download_image(
    file_name: ValidatedFileName,
    flow_id: UUID,
):
    """下载图片并直接用于浏览器渲染。"""
    storage_service = get_storage_service()
    extension = file_name.split(".")[-1]
    flow_id_str = str(flow_id)

    if not extension:
        raise HTTPException(status_code=500, detail=f"Extension not found for file {file_name}")
    try:
        content_type = build_content_type_from_extension(extension)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    if not content_type:
        raise HTTPException(status_code=500, detail=f"Content type not found for extension {extension}")
    if not content_type.startswith("image"):
        raise HTTPException(status_code=500, detail=f"Content type {content_type} is not an image")

    try:
        file_content = await storage_service.get_file(flow_id=flow_id_str, file_name=file_name)
        return StreamingResponse(BytesIO(file_content), media_type=content_type)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/profile_pictures/{folder_name}/{file_name}")
async def download_profile_picture(
    folder_name: str,
    file_name: str,
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
):
    """下载本地头像资源。

    关键路径（三步）：
    1) 校验目录与文件名，防止路径穿越
    2) 先查 `config_dir/profile_pictures`，再回退到包内目录
    3) 读取文件并返回流式响应
    """
    try:
        # 安全：校验输入，阻止目录穿越。
        if ".." in folder_name or ".." in file_name:
            raise HTTPException(
                status_code=400, detail="Path traversal patterns ('..') are not allowed in folder or file names"
            )

        # 安全：仅允许白名单目录名称。
        allowed_folders = _get_allowed_profile_picture_folders(settings_service)
        if folder_name not in allowed_folders:
            raise HTTPException(status_code=400, detail=f"Folder must be one of: {', '.join(sorted(allowed_folders))}")

        # 安全：文件名不可包含路径分隔符。
        if "/" in file_name or "\\" in file_name:
            raise HTTPException(status_code=400, detail="File name cannot contain path separators ('/' or '\\')")

        extension = file_name.split(".")[-1]
        config_dir = settings_service.settings.config_dir
        config_path = Path(config_dir).resolve()  # type: ignore[arg-type]

        # 实现：拼接并规范化路径。
        file_path = (config_path / "profile_pictures" / folder_name / file_name).resolve()

        # 安全：校验路径仍位于允许目录内（包含符号链接场景）。
        allowed_base = (config_path / "profile_pictures").resolve()
        if not str(file_path).startswith(str(allowed_base)):
            # 安全：返回 404 避免泄露目录结构。
            raise HTTPException(status_code=404, detail="Profile picture not found")

        # 实现：config 目录不存在时回退到包内资源。
        if not file_path.exists():
            from langflow.initial_setup import setup

            package_base = Path(setup.__file__).parent / "profile_pictures"
            package_path = (package_base / folder_name / file_name).resolve()

            # 安全：校验包内路径仍在允许目录。
            allowed_package_base = package_base.resolve()
            if not str(package_path).startswith(str(allowed_package_base)):
                # 安全：返回 404 避免泄露目录结构。
                raise HTTPException(status_code=404, detail="Profile picture not found")

            if package_path.exists():
                file_path = package_path
            else:
                raise HTTPException(status_code=404, detail=f"Profile picture {folder_name}/{file_name} not found")

        content_type = build_content_type_from_extension(extension)
        # 实现：使用异步文件读取以避免阻塞。
        file_content = await anyio.Path(file_path).read_bytes()
        return StreamingResponse(BytesIO(file_content), media_type=content_type)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/profile_pictures/list")
async def list_profile_pictures(
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
):
    """列出可用头像文件路径列表。"""
    try:
        config_dir = settings_service.settings.config_dir
        config_path = Path(config_dir)  # type: ignore[arg-type]

        # 实现：基于允许目录集合构建列表。
        allowed_folders = _get_allowed_profile_picture_folders(settings_service)

        results: list[str] = []
        cfg_base = config_path / "profile_pictures"
        if cfg_base.exists():
            for folder in sorted(allowed_folders):
                p = cfg_base / folder
                if p.exists():
                    results += [f"{folder}/{f.name}" for f in p.iterdir() if f.is_file()]

        # 实现：如果配置目录无结果，回退到包内资源。
        if not results:
            from langflow.initial_setup import setup

            package_base = Path(setup.__file__).parent / "profile_pictures"
            for folder in sorted(allowed_folders):
                p = package_base / folder
                if p.exists():
                    results += [f"{folder}/{f.name}" for f in p.iterdir() if f.is_file()]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    return {"files": results}


@router.get("/list/{flow_id}")
async def list_files(
    flow: Annotated[Flow, Depends(get_flow)],
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
):
    """列出流程关联的文件列表。"""
    try:
        files = await storage_service.list_files(flow_id=str(flow.id))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    return {"files": files}


@router.delete("/delete/{flow_id}/{file_name}")
async def delete_file(
    file_name: ValidatedFileName,
    flow: Annotated[Flow, Depends(get_flow)],
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
):
    """删除流程文件。"""
    try:
        await storage_service.delete_file(flow_id=str(flow.id), file_name=file_name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    return {"message": f"File {file_name} deleted successfully"}
