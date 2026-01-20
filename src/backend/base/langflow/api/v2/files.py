"""
模块名称：文件上传/下载与存储协同 API

本模块提供用户文件的上传、下载、改名与删除，并在数据库与存储服务之间保持一致性。
主要功能包括：
- 上传与命名冲突处理（含 `_mcp_servers` 特例）
- 单文件与批量下载/删除
- 读取文件内容与流式传输

关键组件：
- `upload_user_file`：上传并写入元数据
- `download_file` / `download_files_batch`：流式下载
- `delete_file` / `delete_files_batch`：删除并处理永久/临时失败

设计背景：存储服务可能位于外部系统，需区分“存储已失效”与“临时失败”以避免误删元数据。
注意事项：删除流程以存储结果为准，遇到临时错误会保留 DB 记录以便重试。
"""

import io
import re
import uuid
import zipfile
from collections.abc import AsyncGenerator, AsyncIterable
from datetime import datetime
from http import HTTPStatus
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from lfx.log.logger import logger
from sqlmodel import col, select

from langflow.api.schemas import UploadFileResponse
from langflow.api.utils import CurrentActiveUser, DbSession
from langflow.services.database.models.file.model import File as UserFile
from langflow.services.deps import get_settings_service, get_storage_service
from langflow.services.settings.service import SettingsService
from langflow.services.storage.service import StorageService

router = APIRouter(tags=["Files"], prefix="/files")

# 注意：MCP 配置文件使用固定前缀，真实文件名会追加用户 ID
MCP_SERVERS_FILE = "_mcp_servers"
SAMPLE_DATA_DIR = Path(__file__).parent / "sample_data"


def is_permanent_storage_failure(error: Exception) -> bool:
    """判断存储删除失败是否可视为“永久丢失”。

    契约：输入异常对象，返回是否可安全删除 DB 元数据。
    副作用：无。
    失败语义：函数本身不抛异常，仅基于类型/内容判断。

    决策：把“对象不存在”类错误视为永久失败，允许清理 DB
    问题：存储与 DB 可能不一致，需判断是否可删除元数据
    方案：识别 `FileNotFoundError`/S3 `NoSuchBucket|NoSuchKey|404`
    代价：误判会导致元数据提前被删除
    重评：新增存储后端或错误码变化时需要更新规则
    """
    # 注意：本地存储的缺失文件通常是永久失败
    if isinstance(error, FileNotFoundError):
        return True

    # 注意：S3 类错误需要从 `response.Error.Code` 判断对象是否存在
    if hasattr(error, "response"):
        response = error.response
        if isinstance(response, dict):
            error_code = response.get("Error", {}).get("Code")
            # 注意：对象或桶不存在时可视为永久失败
            if error_code in ("NoSuchBucket", "NoSuchKey", "404"):
                return True

    # 注意：兜底匹配文本（低精度），仅覆盖极少数边界异常
    error_str = str(error)
    permanent_patterns = ("NoSuchBucket", "NoSuchKey", "not found", "FileNotFoundError")

    return any(pattern in error_str for pattern in permanent_patterns)


async def get_mcp_file(current_user: CurrentActiveUser, *, extension: bool = False) -> str:
    """生成当前用户的 MCP 配置文件名。

    契约：`extension=True` 时追加 `.json`，否则仅返回文件根名。
    副作用：无。
    失败语义：仅在 `current_user.id` 不可序列化时抛异常。
    """
    return f"{MCP_SERVERS_FILE}_{current_user.id!s}" + (".json" if extension else "")


async def byte_stream_generator(file_input, chunk_size: int = 8192) -> AsyncGenerator[bytes, None]:
    """将 bytes/流对象转换为按块产出的异步生成器。

    契约：支持 `bytes`、带 `read` 的对象、或异步迭代器。
    失败语义：输入不可读时会在迭代过程中抛异常。
    性能：`chunk_size` 过大将增加内存峰值。
    """
    if isinstance(file_input, bytes):
        for i in range(0, len(file_input), chunk_size):
            yield file_input[i : i + chunk_size]
    elif hasattr(file_input, "read"):
        while True:
            chunk = await file_input.read(chunk_size) if callable(file_input.read) else file_input.read(chunk_size)
            if not chunk:
                break
            yield chunk
    else:
        async for chunk in file_input:
            yield chunk


async def fetch_file_object(file_id: uuid.UUID, current_user: CurrentActiveUser, session: DbSession):
    """获取文件元数据并校验归属。

    契约：返回 `UserFile`，若不存在或无权限则抛 `HTTPException(404)`。
    副作用：读取数据库。
    失败语义：统一返回 404，避免暴露资源存在性。

    决策：无权限也返回 404
    问题：防止泄露资源存在信息
    方案：对“无权限”与“不存在”统一返回 404
    代价：排障时需要结合日志与 DB 追踪
    重评：当需要区分权限与不存在时改为 403
    """
    stmt = select(UserFile).where(UserFile.id == file_id)
    results = await session.exec(stmt)
    file = results.first()

    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    if file.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="File not found")

    return file


async def save_file_routine(
    file,
    storage_service,
    current_user: CurrentActiveUser,
    file_content=None,
    file_name=None,
    *,
    append: bool = False,
):
    """保存文件内容到存储服务。

    契约：返回 `(file_id, stored_file_name)`，`file_content` 为空时从 `file` 读取。
    副作用：写入存储系统。
    失败语义：存储层异常向上抛出。
    """
    file_id = uuid.uuid4()

    if not file_content:
        file_content = await file.read()
    if not file_name:
        file_name = file.filename

    await storage_service.save_file(flow_id=str(current_user.id), file_name=file_name, data=file_content, append=append)

    return file_id, file_name


@router.post("", status_code=HTTPStatus.CREATED)
@router.post("/", status_code=HTTPStatus.CREATED)
async def upload_user_file(
    file: Annotated[UploadFile, File(...)],
    session: DbSession,
    current_user: CurrentActiveUser,
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
    *,
    append: bool = False,
) -> UploadFileResponse:
    """上传用户文件并写入元数据。

    契约：输入文件与服务依赖，返回 `UploadFileResponse`。
    关键路径（三步）：
    1) 读取配置并校验空文件/大小上限
    2) 处理命名冲突（含 `_mcp_servers` 与 `append`）
    3) 写入存储并落库，失败时回滚存储
    失败语义：超限返回 413；存储不存在返回 404；权限/数据库异常返回 500。
    排障入口：关注存储删除失败与 DB 插入异常日志。

    决策：普通文件采用“同名递增 (n)”避免覆盖
    问题：重复上传导致同名冲突
    方案：查询同根名并追加计数
    代价：文件名可能被自动调整
    重评：若引入版本化存储则切换为版本号策略
    """
    # 注意：配置单位为 MB，比较时需要换算为字节
    try:
        max_file_size_upload = settings_service.settings.max_file_size_upload
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Settings error: {e}") from e

    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    # 注意：超过上限直接拒绝，避免占满存储或 DB
    if file.size > max_file_size_upload * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail=f"File size is larger than the maximum file size {max_file_size_upload}MB.",
        )

    try:
        # 注意：`_mcp_servers` 走覆盖路径，其他文件需做去重命名
        new_filename = file.filename
        try:
            root_filename, file_extension = new_filename.rsplit(".", 1)
        except ValueError:
            root_filename, file_extension = new_filename, ""

        mcp_file = await get_mcp_file(current_user)
        mcp_file_ext = await get_mcp_file(current_user, extension=True)

        existing_file = None

        if new_filename == mcp_file_ext:
            # 注意：同名配置只保留一份，先清理旧记录避免冲突
            existing_mcp_file = await get_file_by_name(mcp_file, current_user, session)
            if existing_mcp_file:
                await delete_file(existing_mcp_file.id, current_user, session, storage_service)
                # 注意：提前 flush，确保新记录不会触发唯一约束冲突
                await session.flush()
            unique_filename = new_filename
        elif append:
            # 注意：append 模式复用现有文件名，保证落到同一对象
            existing_file = await get_file_by_name(root_filename, current_user, session)
            if existing_file:
                unique_filename = Path(existing_file.path).name
            else:
                unique_filename = f"{root_filename}.{file_extension}" if file_extension else root_filename
        else:
            stmt = select(UserFile).where(
                col(UserFile.name).like(f"{root_filename}%"), UserFile.user_id == current_user.id
            )
            existing_files = await session.exec(stmt)
            files = existing_files.all()

            if files:
                counts = []

                for my_file in files:
                    match = re.search(r"\((\d+)\)(?=\.\w+$|$)", my_file.name)
                    if match:
                        counts.append(int(match.group(1)))

                count = max(counts) if counts else 0
                root_filename = f"{root_filename} ({count + 1})"

            unique_filename = f"{root_filename}.{file_extension}" if file_extension else root_filename

        try:
            file_id, stored_file_name = await save_file_routine(
                file, storage_service, current_user, file_name=unique_filename, append=append
            )
            file_size = await storage_service.get_file_size(
                flow_id=str(current_user.id),
                file_name=stored_file_name,
            )
        except FileNotFoundError as e:
            # 注意：存储桶缺失或对象不存在
            raise HTTPException(status_code=404, detail=str(e)) from e
        except PermissionError as e:
            # 注意：权限/凭证问题属于服务端配置错误
            raise HTTPException(status_code=500, detail="Error accessing storage") from e
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error accessing file: {e}") from e

        if append and existing_file:
            existing_file.size = file_size
            session.add(existing_file)
            await session.commit()
            await session.refresh(existing_file)
            new_file = existing_file
        else:
            new_file = UserFile(
                id=file_id,
                user_id=current_user.id,
                name=root_filename,
                path=f"{current_user.id}/{stored_file_name}",
                size=file_size,
            )

        session.add(new_file)
        try:
            await session.flush()
            await session.refresh(new_file)
        except Exception as db_err:
            # 注意：DB 落库失败需回收存储对象，避免孤儿文件
            try:
                await storage_service.delete_file(flow_id=str(current_user.id), file_name=stored_file_name)
            except OSError as e:
                await logger.aerror(f"Failed to clean up uploaded file {stored_file_name}: {e}")

            raise HTTPException(
                status_code=500, detail=f"Error inserting file metadata into database: {db_err}"
            ) from db_err
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}") from e

    return UploadFileResponse(id=new_file.id, name=new_file.name, path=Path(new_file.path), size=new_file.size)


async def get_file_by_name(
    file_name: str,
    current_user: CurrentActiveUser,
    session: DbSession,
) -> UserFile | None:
    """按文件名获取当前用户的文件元数据。

    契约：返回 `UserFile` 或 `None`。
    副作用：读取数据库。
    失败语义：查询异常转换为 `HTTPException(500)`。
    """
    try:
        stmt = select(UserFile).where(UserFile.user_id == current_user.id).where(UserFile.name == file_name)
        result = await session.exec(stmt)

        return result.first() or None
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching file: {e}") from e


async def load_sample_files(current_user: CurrentActiveUser, session: DbSession, storage_service: StorageService):
    """将样例文件写入存储与 DB（仅在显式调用时执行）。

    契约：按 `SAMPLE_DATA_DIR` 写入缺失的样例文件。
    副作用：写入存储、插入 DB 记录。
    失败语义：异常向上抛出，由调用方决定是否回滚。
    """
    for sample_file_path in Path(SAMPLE_DATA_DIR).iterdir():
        sample_file_name = sample_file_path.name
        root_filename, _ = sample_file_name.rsplit(".", 1)

        existing_sample_file = await get_file_by_name(
            file_name=root_filename, current_user=current_user, session=session
        )
        if existing_sample_file:
            continue

        binary_data = sample_file_path.read_bytes()

        file_id, _ = await save_file_routine(
            sample_file_path,
            storage_service,
            current_user,
            file_content=binary_data,
            file_name=sample_file_name,
        )
        file_size = await storage_service.get_file_size(
            flow_id=str(current_user.id),
            file_name=sample_file_name,
        )
        sample_file = UserFile(
            id=file_id,
            user_id=current_user.id,
            name=root_filename,
            path=sample_file_name,
            size=file_size,
        )

        session.add(sample_file)

        await session.flush()
        await session.refresh(sample_file)


@router.get("")
@router.get("/", status_code=HTTPStatus.OK)
async def list_files(
    current_user: CurrentActiveUser,
    session: DbSession,
    # storage_service: Annotated[StorageService, Depends(get_storage_service)],  # 预留
) -> list[UserFile]:
    """列出当前用户可见的文件。

    契约：返回不包含 MCP 配置文件的文件列表。
    副作用：读取数据库。
    失败语义：查询失败返回 `HTTPException(500)`。

    决策：隐藏 `_mcp_servers` 配置文件
    问题：避免配置文件对用户列表造成干扰或泄露
    方案：过滤文件名为 MCP 配置前缀的记录
    代价：需要额外过滤逻辑
    重评：若将配置移入专用表，可移除此过滤
    """
    try:
        # TODO：样例文件加载待进一步测试
        # await load_sample_files(current_user, session, get_storage_service())
        stmt = select(UserFile).where(UserFile.user_id == current_user.id)
        results = await session.exec(stmt)

        full_list = list(results)

        mcp_file = await get_mcp_file(current_user)

        return [file for file in full_list if file.name != mcp_file]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing files: {e}") from e


@router.delete("/batch/", status_code=HTTPStatus.OK)
async def delete_files_batch(
    file_ids: list[uuid.UUID],
    current_user: CurrentActiveUser,
    session: DbSession,
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
):
    """按 ID 批量删除文件。

    契约：仅删除当前用户的文件，返回汇总消息。
    关键路径（三步）：
    1) 批量查询元数据并校验归属
    2) 先删存储，再删 DB（永久失败允许继续）
    3) 汇总成功/保留/失败并返回
    失败语义：全部 DB 删除失败时返回 500；其余情况返回 200 并记录日志。
    排障入口：日志关键字 `storage failures` / `database failures`。

    决策：区分存储“永久/临时”失败以决定是否删除 DB
    问题：存储与 DB 可能短暂不一致
    方案：永久失败删除 DB，临时失败保留以便重试
    代价：需要维护错误分类逻辑
    重评：当存储具备事务或幂等删除保障时评估简化
    """
    try:
        stmt = select(UserFile).where(col(UserFile.id).in_(file_ids), col(UserFile.user_id) == current_user.id)
        results = await session.exec(stmt)
        files = results.all()

        if not files:
            raise HTTPException(status_code=404, detail="No files found")

        storage_failures = []
        db_failures = []

        for file in files:
            file_name = Path(file.path).name
            storage_deleted = False

            try:
                await storage_service.delete_file(flow_id=str(current_user.id), file_name=file_name)
                storage_deleted = True
            except OSError as err:
                # 注意：永久失败视为存储已不存在，可继续删除 DB
                if is_permanent_storage_failure(err):
                    await logger.awarning(
                        "File %s not found in storage (permanent failure), will remove from database: %s",
                        file_name,
                        err,
                    )
                    storage_deleted = True  # 注意：对 DB 视为已删除
                else:
                    # 注意：临时失败（网络/权限）保留 DB 以便重试
                    storage_failures.append(f"{file_name}: {err}")
                    await logger.awarning(
                        "Failed to delete file %s from storage (transient error, keeping in database for retry): %s",
                        file_name,
                        err,
                    )

            if storage_deleted:
                try:
                    await session.delete(file)
                except OSError as db_error:
                    db_failures.append(f"{file_name}: {db_error}")
                    await logger.aerror(
                        "Failed to delete file %s from database: %s",
                        file_name,
                        db_error,
                    )

        if storage_failures:
            await logger.awarning(
                "Batch delete completed with %d storage failures: %s", len(storage_failures), storage_failures
            )
        if db_failures:
            await logger.aerror("Batch delete completed with %d database failures: %s", len(db_failures), db_failures)
            if len(db_failures) == len(files):
                raise HTTPException(status_code=500, detail=f"Failed to delete any files from database: {db_failures}")

        # 注意：删除数=总数-存储临时失败-DB 删除失败
        files_deleted = len(files) - len(storage_failures) - len(db_failures)
        files_kept = len(storage_failures)  # 注意：存储临时失败将保留 DB 记录

        if files_deleted == len(files):
            message = f"{files_deleted} files deleted successfully"
        elif files_deleted > 0:
            message = f"{files_deleted} files deleted successfully"
            if files_kept > 0:
                message += f", {files_kept} files kept in database due to transient storage errors (can retry)"
        else:
            message = "No files were deleted from database"

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting files: {e}") from e

    return {"message": message}


@router.post("/batch/", status_code=HTTPStatus.OK)
async def download_files_batch(
    file_ids: list[uuid.UUID],
    current_user: CurrentActiveUser,
    session: DbSession,
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
):
    """按 ID 批量下载文件并打包为 ZIP。

    契约：返回 `StreamingResponse`，ZIP 内文件名为原始名称+扩展名。
    副作用：从存储读取文件内容并在内存构建 ZIP。
    失败语义：文件不存在返回 404；其余异常返回 500。
    性能：所有文件内容会进入内存，批量大文件可能占用较高内存。

    决策：使用内存 ZIP 流而非临时文件
    问题：需要在无本地磁盘依赖下完成打包
    方案：使用 `BytesIO` 作为 ZIP 容器
    代价：大文件集合会占用较高内存
    重评：当批量体积增大或需要落盘审计时改为临时文件
    """
    try:
        stmt = select(UserFile).where(col(UserFile.id).in_(file_ids), col(UserFile.user_id) == current_user.id)
        results = await session.exec(stmt)
        files = results.all()

        if not files:
            raise HTTPException(status_code=404, detail="No files found")

        zip_stream = io.BytesIO()

        with zipfile.ZipFile(zip_stream, "w") as zip_file:
            for file in files:
                file_content = await storage_service.get_file(
                    flow_id=str(current_user.id), file_name=Path(file.path).name
                )

                file_extension = Path(file.path).suffix
                filename_with_extension = f"{file.name}{file_extension}"

                zip_file.writestr(filename_with_extension, file_content)

        zip_stream.seek(0)

        current_time = datetime.now(tz=ZoneInfo("UTC")).astimezone().strftime("%Y%m%d_%H%M%S")
        filename = f"{current_time}_langflow_files.zip"

        return StreamingResponse(
            zip_stream,
            media_type="application/x-zip-compressed",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=f"File not found: {e}") from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error downloading files: {e}") from e


async def read_file_content(file_stream: AsyncIterable[bytes] | bytes, *, decode: bool = True) -> str | bytes:
    """读取文件内容并按需解码。

    契约：输入 `bytes` 或异步字节流，`decode=True` 返回 `str`。
    副作用：无。
    失败语义：非字节块触发 `HTTPException(500)`；解码失败返回 500。
    """
    content = b""
    try:
        if isinstance(file_stream, bytes):
            content = file_stream
        else:
            async for chunk in file_stream:
                if not isinstance(chunk, bytes):
                    msg = "File stream must yield bytes"
                    raise TypeError(msg)
                content += chunk
        if not decode:
            return content
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=500, detail="Invalid file encoding") from exc
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=f"Error reading file: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error reading file: {exc}") from exc


@router.get("/{file_id}")
async def download_file(
    file_id: uuid.UUID,
    current_user: CurrentActiveUser,
    session: DbSession,
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
    *,
    return_content: bool = False,
):
    """按 ID 下载文件或返回内容文本。

    契约：`return_content=True` 返回 `str`；否则返回 `StreamingResponse`。
    关键路径（三步）：
    1) 查询元数据并校验归属
    2) 预检存储对象存在性
    3) 构造流式响应并返回
    失败语义：不存在返回 404；其余异常返回 500。

    决策：在开始流式响应前先校验对象存在
    问题：流式响应一旦开始无法更改状态码
    方案：调用 `get_file_size` 触发提前错误
    代价：额外一次存储调用
    重评：若存储支持响应前校验，可移除此预检
    """
    try:
        file = await fetch_file_object(file_id, current_user, session)
        if not file:
            raise HTTPException(status_code=404, detail="File not found")

        file_name = Path(file.path).name

        if return_content:
            file_content = await storage_service.get_file(flow_id=str(current_user.id), file_name=file_name)
            if file_content is None:
                raise HTTPException(status_code=404, detail="File not found")
            return await read_file_content(file_content, decode=True)

        # 注意：流式响应一旦开始无法再修改状态码，需提前校验
        try:
            await storage_service.get_file_size(flow_id=str(current_user.id), file_name=file_name)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=f"File not found: {e}") from e

        file_stream = storage_service.get_file_stream(flow_id=str(current_user.id), file_name=file_name)
        byte_stream = byte_stream_generator(file_stream)

        file_extension = Path(file.path).suffix
        filename_with_extension = f"{file.name}{file_extension}"

        return StreamingResponse(
            byte_stream,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{filename_with_extension}"'},
        )

    except HTTPException:
        raise
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=f"File not found: {e}") from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error downloading file: {e}") from e


@router.put("/{file_id}")
async def edit_file_name(
    file_id: uuid.UUID,
    name: str,
    current_user: CurrentActiveUser,
    session: DbSession,
) -> UploadFileResponse:
    """按 ID 修改文件显示名。

    契约：返回更新后的 `UploadFileResponse`。
    副作用：写入数据库。
    失败语义：异常转换为 `HTTPException(500)`。

    决策：仅修改显示名，不变更存储路径
    问题：避免移动底层对象导致成本或失败
    方案：更新 `UserFile.name` 而不改 `path`
    代价：显示名与存储对象名可能脱节
    重评：需要同步重命名存储对象时调整
    """
    try:
        file = await fetch_file_object(file_id, current_user, session)

        file.name = name
        session.add(file)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error editing file: {e}") from e

    return UploadFileResponse(id=file.id, name=file.name, path=file.path, size=file.size)


@router.delete("/{file_id}")
async def delete_file(
    file_id: uuid.UUID,
    current_user: CurrentActiveUser,
    session: DbSession,
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
):
    """按 ID 删除单个文件。

    契约：删除存储对象与 DB 记录，返回删除结果消息。
    关键路径（三步）：
    1) 查询并校验归属
    2) 先删存储（永久失败允许继续）
    3) 删 DB 并返回结果
    失败语义：存储临时失败返回 500；DB 删除失败返回 500。

    决策：存储删除失败时区分永久/临时
    问题：存储对象可能已被外部删除
    方案：永久失败允许清理 DB，临时失败保留重试
    代价：需要维护错误码规则
    重评：存储端支持幂等删除或强一致后调整
    """
    try:
        file_to_delete = await fetch_file_object(file_id, current_user, session)
        if not file_to_delete:
            raise HTTPException(status_code=404, detail="File not found")

        file_name = Path(file_to_delete.path).name

        storage_deleted = False
        try:
            await storage_service.delete_file(flow_id=str(current_user.id), file_name=file_name)
            storage_deleted = True
        except Exception as err:
            # 注意：永久失败可继续删除 DB；临时失败需保留以便重试
            if is_permanent_storage_failure(err):
                await logger.awarning(
                    "File %s not found in storage (permanent failure), will remove from database: %s",
                    file_name,
                    err,
                )
                storage_deleted = True
            else:
                await logger.awarning(
                    "Failed to delete file %s from storage (transient error, keeping in database for retry): %s",
                    file_name,
                    err,
                )
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to delete file from storage. Please try again. Error: {err}",
                ) from err

        if storage_deleted:
            try:
                await session.delete(file_to_delete)
            except Exception as db_error:
                await logger.aerror(
                    "Failed to delete file %s from database: %s",
                    file_to_delete.name,
                    db_error,
                )
                raise HTTPException(
                    status_code=500, detail=f"Error deleting file from database: {db_error}"
                ) from db_error

            return {"detail": f"File {file_to_delete.name} deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        await logger.aerror("Error deleting file %s: %s", file_id, e)
        raise HTTPException(status_code=500, detail=f"Error deleting file: {e}") from e


@router.delete("")
@router.delete("/", status_code=HTTPStatus.OK)
async def delete_all_files(
    current_user: CurrentActiveUser,
    session: DbSession,
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
):
    """删除当前用户的全部文件。

    契约：批量删除存储与 DB，返回汇总消息。
    关键路径（三步）：
    1) 查询全部元数据
    2) 逐个删除存储并处理永久/临时失败
    3) 删除 DB 并汇总结果
    失败语义：全部 DB 删除失败时返回 500；其余情况返回 200 并记录日志。

    决策：存储失败按永久/临时分类
    问题：存储删除结果不确定时是否清理 DB
    方案：永久失败删除 DB，临时失败保留
    代价：批量流程需要维护失败列表
    重评：当存储具备批量事务时评估简化
    """
    try:
        stmt = select(UserFile).where(UserFile.user_id == current_user.id)
        results = await session.exec(stmt)
        files = results.all()

        storage_failures = []
        db_failures = []

        for file in files:
            file_name = Path(file.path).name
            storage_deleted = False

            try:
                await storage_service.delete_file(flow_id=str(current_user.id), file_name=file_name)
                storage_deleted = True
            except OSError as err:
                if is_permanent_storage_failure(err):
                    await logger.awarning(
                        "File %s not found in storage, also removing from database: %s",
                        file_name,
                        err,
                    )
                    storage_deleted = True
                else:
                    storage_failures.append(f"{file_name}: {err}")
                    await logger.awarning(
                        "Failed to delete file %s from storage (transient error, keeping in database for retry): %s",
                        file_name,
                        err,
                    )

            if storage_deleted:
                try:
                    await session.delete(file)
                except OSError as db_error:
                    db_failures.append(f"{file_name}: {db_error}")
                    await logger.aerror(
                        "Failed to delete file %s from database: %s",
                        file_name,
                        db_error,
                    )

        if storage_failures:
            await logger.awarning(
                "Batch delete completed with %d storage failures: %s", len(storage_failures), storage_failures
            )

        if db_failures:
            await logger.aerror("Batch delete completed with %d database failures: %s", len(db_failures), db_failures)
            if len(db_failures) == len(files):
                raise HTTPException(status_code=500, detail=f"Failed to delete any files from database: {db_failures}")

        # 注意：删除数=总数-存储临时失败-DB 删除失败
        files_deleted = len(files) - len(storage_failures) - len(db_failures)
        files_kept = len(storage_failures) + len(db_failures)

        if files_deleted == len(files):
            message = f"All {files_deleted} files deleted successfully"
        elif files_deleted > 0:
            message = f"{files_deleted} files deleted successfully"
            if files_kept > 0:
                message += f", {files_kept} files failed to delete. See logs for details."
        else:
            message = "Failed to delete files. See logs for details."

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting all files: {e}") from e

    return {"message": message}
