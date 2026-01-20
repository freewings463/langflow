"""
模块名称：作业队列服务

本模块提供按 `job_id` 管理的异步队列与任务清理能力，用于隔离作业消息通道与任务生命周期。主要功能包括：
- 创建作业队列并绑定事件管理器
- 启动作业处理任务并替换旧任务
- 周期扫描失败/取消任务并按宽限期回收

关键组件：
- `JobQueueService`：队列注册、任务生命周期与清理策略
- `JobQueueNotFoundError`：缺失队列的失败语义

使用场景：异步作业需要独立消息队列与事件回调的后台执行流程。
设计背景：异步执行流需要队列隔离与可控回收，避免任务取消后立即清理导致观测缺失。
注意事项：清理由 60 秒周期触发，失败任务需等待 300 秒宽限期才会移除。
"""

from __future__ import annotations

import asyncio

from lfx.log.logger import logger

from langflow.events.event_manager import EventManager
from langflow.services.base import Service


class JobQueueNotFoundError(Exception):
    """作业队列缺失时抛出的异常。

    契约：异常包含 `job_id` 便于上层记录与排障。
    决策：用专用异常而非返回 `None`。
    问题：调用方难以区分“无队列”和“队列存在但为空”。
    方案：抛 `JobQueueNotFoundError` 强制处理缺失场景。
    代价：调用方需要增加异常处理分支。
    重评：当调用链明确需要容错路径时可改为可选返回。
    """

    def __init__(self, job_id: str) -> None:
        """初始化异常上下文。

        契约：输入 `job_id`，无返回值；副作用：构造包含 `job_id` 的错误信息。
        决策：将 `job_id` 写入异常实例以便排障关联。
        问题：仅依赖字符串消息不利于结构化日志与追踪关联。
        方案：保存 `self.job_id` 并在消息中包含 `job_id`。
        代价：异常对象携带额外字段，序列化需关注字段暴露。
        重评：当异常传输需要最小化字段时评估移除该字段。
        """
        self.job_id = job_id
        super().__init__(f"Job queue not found for job_id: {job_id}")


class JobQueueService(Service):
    """作业队列与任务生命周期管理服务。

    契约：以 `job_id` 为键注册队列；`create_queue` 返回 (`asyncio.Queue`, `EventManager`)；`start_job` 绑定协程任务；失败语义见各方法。
    副作用：创建后台清理任务、取消旧任务、注册事件。
    关键路径（三步）：1) 创建队列 2) 启动任务 3) 周期扫描并按 300 秒宽限清理。
    排障：日志关键字 `JobQueueService` / `cleanup` / `job_id`。
    决策：采用“失败/取消后延迟清理”的两阶段回收。
    问题：立即清理会导致上游事件、日志或追踪丢失。
    方案：记录清理时间戳并在 `CLEANUP_GRACE_PERIOD` 后释放。
    代价：失败队列会额外占用内存最多 300 秒。
    重评：当内存压力或失败率显著升高时评估缩短宽限期。
    """

    name = "job_queue_service"

    def __init__(self) -> None:
        """初始化作业队列注册表与清理策略。

        契约：无输入，返回 `None`；副作用：初始化 `_queues`、清理任务句柄并设置宽限期。
        关键路径：设置 `_queues`、`_cleanup_task`、`_closed` 与 `CLEANUP_GRACE_PERIOD`。
        决策：默认宽限期设为 300 秒。
        问题：立即清理会影响上游观测与延迟完成的回调。
        方案：以固定宽限期在失败后延迟回收。
        代价：失败队列在宽限期内占用内存。
        重评：当运行环境内存压力明显上升时调整该默认值。
        """
        self._queues: dict[str, tuple[asyncio.Queue, EventManager, asyncio.Task | None, float | None]] = {}
        self._cleanup_task: asyncio.Task | None = None
        self._closed = False
        self.ready = False
        self.CLEANUP_GRACE_PERIOD = 300

    def is_started(self) -> bool:
        """判断后台清理任务是否已创建。

        契约：无输入，返回 `bool`；副作用：无；失败语义：无。
        关键路径：检查 `_cleanup_task` 是否为 `None`。
        决策：用清理任务句柄作为启动标志。
        问题：额外状态字段可能与任务状态不一致。
        方案：以 `_cleanup_task` 存在性作为唯一判断。
        代价：若任务被外部取消可能出现误判。
        重评：当需要区分“已启动但暂停”时改为显式状态。
        """
        return self._cleanup_task is not None

    def set_ready(self) -> None:
        """标记服务就绪，必要时启动后台清理。

        契约：无输入，无返回值；副作用：可能创建清理任务并更新就绪状态。
        关键路径：若未启动先调用 `start`，再调用父类 `set_ready`。
        决策：在 `set_ready` 内部进行惰性启动。
        问题：服务就绪与清理任务启动时序可能不同步。
        方案：将启动逻辑内聚到就绪流程，降低调用方负担。
        代价：`set_ready` 变为有副作用的方法。
        重评：当需要显式控制启动时序时拆分调用。
        """
        if not self.is_started():
            self.start()
        super().set_ready()

    def start(self) -> None:
        """启动后台清理任务。

        契约：无输入，无返回值；副作用：创建周期清理任务并写入调试日志；失败语义：任务创建失败将向上抛出异常。
        关键路径：设置 `_closed=False` 并创建 `_periodic_cleanup` 任务。
        决策：使用后台 `asyncio` 任务实现周期清理。
        问题：在请求路径清理会阻塞作业处理主流程。
        方案：异步后台任务每 60 秒触发清理。
        代价：清理触发存在最多 60 秒延迟。
        重评：当需要更实时回收时评估事件驱动清理。
        排障：日志关键字 `JobQueueService started`。
        """
        self._closed = False
        self._cleanup_task = asyncio.create_task(self._periodic_cleanup())
        logger.debug("JobQueueService started: periodic cleanup task initiated.")

    async def stop(self) -> None:
        """停止服务并释放全部作业资源。

        契约：无输入，返回 `None`；副作用：取消后台清理任务与所有作业任务，清空队列；失败语义：清理任务异常将重新抛出。
        关键路径（三步）：
        1) 标记关闭并取消后台清理任务
        2) 等待清理任务退出，异常则向上传递
        3) 逐个调用 `cleanup_job` 释放队列

        异常流：清理任务异常会重新抛出；`cleanup_job` 仅记录任务异常。
        决策：清理任务异常向上抛出而非吞掉。
        问题：静默失败会掩盖后台清理崩溃，导致资源泄漏难以发现。
        方案：检查清理任务异常并在停止阶段重新抛出。
        代价：停止过程可能被异常中断，调用方需处理失败。
        重评：当停止必须兜底完成时改为记录并继续清理。
        排障入口：日志关键字 `JobQueueService stopped` / `cleanup`。
        """
        self._closed = True
        if self._cleanup_task:
            self._cleanup_task.cancel()
            await asyncio.wait([self._cleanup_task])
            if not self._cleanup_task.cancelled():
                exc = self._cleanup_task.exception()
                if exc is not None:
                    raise exc

        for job_id in list(self._queues.keys()):
            await self.cleanup_job(job_id)
        await logger.adebug("JobQueueService stopped: all job queues have been cleaned up.")

    async def teardown(self) -> None:
        """服务关闭钩子。

        契约：无输入，无返回值；副作用：同 `stop`。
        关键路径：直接调用 `stop`。
        决策：复用 `stop` 作为唯一清理入口。
        问题：重复实现清理逻辑易产生行为不一致。
        方案：`teardown` 仅做代理转发。
        代价：`teardown` 无法提供差异化清理策略。
        重评：当需要区分关闭语义时再拆分实现。
        """
        await self.stop()

    def create_queue(self, job_id: str) -> tuple[asyncio.Queue, EventManager]:
        """为 `job_id` 创建队列与事件管理器。

        契约：输入 `job_id`；返回 (`asyncio.Queue`, `EventManager`)；副作用：注册到内部 `_queues` 并初始化事件类型集合；失败语义：服务关闭抛 `RuntimeError`，重复 `job_id` 抛 `ValueError`。
        关键路径：校验服务状态与重复性 → 创建队列与事件管理器 → 写入 `_queues`。
        决策：每个 `job_id` 仅允许一个主队列。
        问题：并行创建多个队列会导致事件分流与清理竞态。
        方案：检测重复并拒绝创建。
        代价：调用方需要显式销毁旧队列再重建。
        重评：当需要分片队列时引入 `job_id`+分片键的复合键。
        """
        if self._closed:
            msg = "Queue service is closed"
            raise RuntimeError(msg)

        existing_queue = self._queues.get(job_id)
        if existing_queue:
            msg = f"Queue for job_id {job_id} already exists"
            raise ValueError(msg)

        main_queue: asyncio.Queue = asyncio.Queue()
        event_manager: EventManager = self._create_default_event_manager(main_queue)

        # 注意：初始化时不绑定任务，允许先入队再启动消费协程。
        self._queues[job_id] = (main_queue, event_manager, None, None)
        logger.debug(f"Queue and event manager successfully created for job_id {job_id}")
        return main_queue, event_manager

    def start_job(self, job_id: str, task_coro) -> None:
        """为 `job_id` 启动处理协程并替换旧任务。

        契约：输入 `job_id` 与协程对象；无返回值；副作用：取消旧任务并启动新任务；失败语义：队列不存在抛 `ValueError`，服务关闭抛 `RuntimeError`。
        关键路径：校验队列 → 取消旧任务 → 创建新任务并更新注册。
        决策：取消旧任务但不在此处等待完成。
        问题：等待旧任务完成会阻塞新作业启动。
        方案：立即创建新任务，旧任务由取消流程自行收敛。
        代价：短时间内可能存在并发任务争抢资源。
        重评：当任务必须串行执行时改为等待旧任务结束。
        排障：日志关键字 `New task started` / `Existing task`。
        """
        if job_id not in self._queues:
            msg = f"No queue found for job_id {job_id}"
            logger.error(msg)
            raise ValueError(msg)

        if self._closed:
            msg = "Queue service is closed"
            logger.error(msg)
            raise RuntimeError(msg)

        main_queue, event_manager, existing_task, _ = self._queues[job_id]

        if existing_task and not existing_task.done():
            logger.debug(f"Existing task for job_id {job_id} detected; cancelling it.")
            existing_task.cancel()

        task = asyncio.create_task(task_coro)
        self._queues[job_id] = (main_queue, event_manager, task, None)
        logger.debug(f"New task started for job_id {job_id}")

    def get_queue_data(self, job_id: str) -> tuple[asyncio.Queue, EventManager, asyncio.Task | None, float | None]:
        """获取队列、事件管理器与任务状态。

        契约：输入 `job_id`；返回 (`asyncio.Queue`, `EventManager`, `asyncio.Task | None`, `cleanup_time | None`)；失败语义：服务关闭抛 `RuntimeError`，不存在抛 `JobQueueNotFoundError`。
        关键路径：先检查服务状态，再从 `_queues` 取值。
        决策：缺失时抛 `JobQueueNotFoundError`。
        问题：返回空值会隐藏配置或生命周期错误。
        方案：通过异常强制调用方处理缺失。
        代价：调用方需显式捕获异常。
        重评：当调用场景以“可选结果”为主时改为返回 `None`。
        """
        if self._closed:
            msg = f"Queue service is closed for job_id: {job_id}"
            raise RuntimeError(msg)

        try:
            return self._queues[job_id]
        except KeyError as exc:
            raise JobQueueNotFoundError(job_id) from exc

    async def cleanup_job(self, job_id: str) -> None:
        """清理指定 `job_id` 的队列与任务资源。

        契约：输入 `job_id`；无返回值；副作用：取消活跃任务、清空队列并移除注册项。
        关键路径（三步）：
        1) 若队列不存在直接返回并记录调试日志
        2) 取消任务并等待结束，异常仅记录
        3) 通过 `get_nowait` 清空队列并移除注册

        异常流：任务异常只记录不抛出；`asyncio.QueueEmpty` 用于停止清空。
        决策：使用非阻塞清空与任务取消的组合清理。
        问题：阻塞清空会挂起清理任务并影响其它队列回收。
        方案：`get_nowait` 清空并仅记录任务异常。
        代价：清空期间若有并发入队可能遗漏，需要上层停止生产。
        重评：当需要强一致清空时改为加锁或协调停止生产。
        排障入口：日志关键字 `cleanup` / `Removed` / `Cancelling active task`。
        """
        if job_id not in self._queues:
            await logger.adebug(f"No queue found for job_id {job_id} during cleanup.")
            return

        await logger.adebug(f"Commencing cleanup for job_id {job_id}")
        main_queue, _event_manager, task, _ = self._queues[job_id]

        # 注意：只等待任务取消完成，任务内部异常仅记录不再抛出。
        if task and not task.done():
            await logger.adebug(f"Cancelling active task for job_id {job_id}")
            task.cancel()
            await asyncio.wait([task])
            if exc := task.exception():
                await logger.aerror(f"Error in task for job_id {job_id}: {exc}")
            await logger.adebug(f"Task cancellation complete for job_id {job_id}")

        # 注意：使用 `get_nowait` 逐项清空，避免阻塞等待并发消费者。
        items_cleared = 0
        while not main_queue.empty():
            try:
                main_queue.get_nowait()
                items_cleared += 1
            except asyncio.QueueEmpty:
                break

        await logger.adebug(f"Removed {items_cleared} items from queue for job_id {job_id}")
        self._queues.pop(job_id, None)
        await logger.adebug(f"Cleanup successful for job_id {job_id}: resources have been released.")

    async def _periodic_cleanup(self) -> None:
        """后台周期清理循环。

        契约：无输入，持续运行直至服务关闭；副作用：每 60 秒触发 `_cleanup_old_queues`。
        关键路径：休眠 60 秒 → 扫描清理 → 捕获异常并记录。
        异常流：接收到 `asyncio.CancelledError` 时重新抛出，其它异常仅记录。
        决策：采用固定间隔轮询而非逐队列定时器。
        问题：逐队列定时器会增加任务数量与调度复杂度。
        方案：单一后台循环统一调度清理。
        代价：清理触发存在最多 60 秒延迟。
        重评：当队列数量显著增加或需秒级回收时引入细粒度调度。
        排障入口：日志关键字 `Periodic cleanup`。
        """
        while not self._closed:
            try:
                await asyncio.sleep(60)
                await self._cleanup_old_queues()
            except asyncio.CancelledError:
                await logger.adebug("Periodic cleanup task received cancellation signal.")
                raise
            except Exception as exc:  # noqa: BLE001
                await logger.aerror(f"Exception encountered during periodic cleanup: {exc}")

    async def _cleanup_old_queues(self) -> None:
        """扫描队列并按宽限期回收失败任务。

        契约：无输入；仅修改 `_queues`。
        副作用：为失败/取消的任务写入清理时间戳并在宽限期后触发 `cleanup_job`。
        失败语义：不抛异常，依赖日志观测。
        决策：仅对取消或异常完成的任务触发清理。
        问题：成功任务可能仍需保留队列供下游拉取结果。
        方案：仅标记取消/失败任务并延迟清理。
        代价：成功任务若未显式清理会长期占用内存。
        重评：当成功任务无需保留时改为成功后自动清理。
        注意：正常完成的任务不会自动清理，需调用方显式清理或等待外部流程。
        """
        current_time = asyncio.get_running_loop().time()

        for job_id in list(self._queues.keys()):
            _, _, task, cleanup_time = self._queues[job_id]
            if task:
                await logger.adebug(
                    f"Queue {job_id} status - Done: {task.done()}, "
                    f"Cancelled: {task.cancelled()}, "
                    f"Has exception: {task.exception() is not None if task.done() else 'N/A'}"
                )

                if task and (task.cancelled() or (task.done() and task.exception() is not None)):
                    if cleanup_time is None:
                        # 注意：先记录清理时间，避免失败后立即删除导致上游观测丢失。
                        self._queues[job_id] = (
                            self._queues[job_id][0],
                            self._queues[job_id][1],
                            self._queues[job_id][2],
                            current_time,
                        )
                        await logger.adebug(
                            f"Job queue for job_id {job_id} marked for cleanup - Task cancelled or failed"
                        )
                    elif current_time - cleanup_time >= self.CLEANUP_GRACE_PERIOD:
                        # 注意：宽限期到期后才执行实际清理，降低并发读写冲突风险。
                        await logger.adebug(f"Cleaning up job_id {job_id} after grace period")
                        await self.cleanup_job(job_id)

    def _create_default_event_manager(self, queue: asyncio.Queue) -> EventManager:
        """构建默认事件管理器并注册事件类型。

        契约：输入 `queue`；返回 `EventManager`；副作用：注册固定事件名与类型映射（如 `on_token`/`token` 等）；失败语义：事件注册失败会抛出异常。
        关键路径：构造 `EventManager` → 注册事件映射列表。
        决策：预注册固定事件列表而非运行时动态注册。
        问题：动态注册容易导致事件缺失且难以排查。
        方案：在创建时统一注册已知事件类型。
        代价：扩展事件需修改代码并重新发布。
        重评：当事件类型频繁变化时引入配置化注册。
        """
        manager = EventManager(queue)
        event_names_types = [
            ("on_token", "token"),
            ("on_vertices_sorted", "vertices_sorted"),
            ("on_error", "error"),
            ("on_end", "end"),
            ("on_message", "add_message"),
            ("on_remove_message", "remove_message"),
            ("on_end_vertex", "end_vertex"),
            ("on_build_start", "build_start"),
            ("on_build_end", "build_end"),
        ]
        for name, event_type in event_names_types:
            manager.register_event(name, event_type)
        return manager
