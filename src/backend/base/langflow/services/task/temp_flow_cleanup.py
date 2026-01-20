"""
模块名称：临时 `Flow` 清理任务

本模块提供孤儿记录清理与后台清理工作线程，主要用于清理引用不存在 `Flow` 的记录与文件。主要功能包括：
- 扫描数据库中引用不存在 `Flow` 的记录并删除
- 删除对应存储目录下的文件
- 周期性执行清理任务

关键组件：
- `cleanup_orphaned_records`：孤儿记录与文件清理
- `CleanupWorker`：后台清理协程

设计背景：防止临时/已删除 `Flow` 产生的脏数据长期堆积
注意事项：清理包含数据库与存储双重操作；失败时仅记录日志不终止循环
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

from lfx.log.logger import logger
from sqlmodel import col, delete, select

from langflow.services.database.models.message.model import MessageTable
from langflow.services.database.models.transactions.model import TransactionTable
from langflow.services.database.models.vertex_builds.model import VertexBuildTable
from langflow.services.deps import get_settings_service, get_storage_service, session_scope

if TYPE_CHECKING:
    from langflow.services.storage.service import StorageService


async def cleanup_orphaned_records() -> None:
    """清理引用不存在 `Flow` 的孤儿记录与文件。

    关键路径（三步）：
    1) 查询现存 `Flow` 列表并定位各表孤儿 `flow_id`
    2) 删除孤儿记录并清理存储文件
    3) 记录清理结果与异常日志
    异常流：数据库/存储操作异常被捕获并记录，不中断整体清理。
    性能瓶颈：全表扫描与存储文件遍历。
    排障入口：日志关键字 `orphaned flow IDs`/`Failed to delete file`。
    """
    from langflow.services.database.models.flow.model import Flow

    async with session_scope() as session:
        flow_ids_subquery = select(Flow.id)

        tables: list[type[VertexBuildTable | MessageTable | TransactionTable]] = [
            MessageTable,
            VertexBuildTable,
            TransactionTable,
        ]

        for table in tables:
            try:
                orphaned_flow_ids = (
                    await session.exec(
                        select(col(table.flow_id).distinct()).where(col(table.flow_id).not_in(flow_ids_subquery))
                    )
                ).all()

                if orphaned_flow_ids:
                    logger.debug(f"Found {len(orphaned_flow_ids)} orphaned flow IDs in {table.__name__}")

                    await session.exec(delete(table).where(col(table.flow_id).in_(orphaned_flow_ids)))

                    storage_service: StorageService = get_storage_service()
                    for flow_id in orphaned_flow_ids:
                        try:
                            files = await storage_service.list_files(str(flow_id))
                            for file in files:
                                try:
                                    await storage_service.delete_file(str(flow_id), file)
                                except Exception as exc:  # noqa: BLE001
                                    logger.error(f"Failed to delete file {file} for flow {flow_id}: {exc!s}")
                            flow_dir = storage_service.data_dir / str(flow_id)
                            if await flow_dir.exists():
                                await flow_dir.rmdir()
                        except Exception as exc:  # noqa: BLE001
                            logger.error(f"Failed to list files for flow {flow_id}: {exc!s}")

                    logger.debug(f"Successfully deleted orphaned records from {table.__name__}")

            except Exception as exc:  # noqa: BLE001
                logger.error(f"Error cleaning up orphaned records in {table.__name__}: {exc!s}")


class CleanupWorker:
    """后台清理工作协程封装。"""

    def __init__(self) -> None:
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def start(self):
        """启动清理协程。

        契约：重复启动时仅记录告警。
        """
        if self._task is not None:
            await logger.awarning("Cleanup worker is already running")
            return

        self._task = asyncio.create_task(self._run())
        await logger.adebug("Started database cleanup worker")

    async def stop(self):
        """优雅停止清理协程。"""
        if self._task is None:
            await logger.awarning("Cleanup worker is not running")
            return

        await logger.adebug("Stopping database cleanup worker...")
        self._stop_event.set()
        await self._task
        self._task = None
        await logger.adebug("Database cleanup worker stopped")

    async def _run(self):
        """循环执行清理任务直到停止。

        关键路径（三步）：
        1) 执行 `cleanup_orphaned_records`
        2) 等待清理间隔或停止事件
        3) 清理挂起的等待任务
        异常流：清理或等待异常时记录日志并最小化休眠。
        性能瓶颈：数据库清理与存储遍历。
        排障入口：日志关键字 `Error in cleanup worker`.
        """
        settings = get_settings_service().settings
        while not self._stop_event.is_set():
            try:
                await cleanup_orphaned_records()
            except Exception as exc:  # noqa: BLE001
                await logger.aerror(f"Error in cleanup worker: {exc!s}")

            try:
                sleep_task = asyncio.create_task(asyncio.sleep(settings.public_flow_cleanup_interval))
                stop_task = asyncio.create_task(self._stop_event.wait())

                done, pending = await asyncio.wait([sleep_task, stop_task], return_when=asyncio.FIRST_COMPLETED)

                for task in pending:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task

                if stop_task in done:
                    break

            except Exception as exc:  # noqa: BLE001
                logger.error(f"Error in cleanup worker sleep: {exc!s}")
                await asyncio.sleep(60)


cleanup_worker = CleanupWorker()
