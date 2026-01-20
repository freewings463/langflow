"""
模块名称：`AnyIO` 任务后端

本模块提供基于 `AnyIO` 的任务执行与结果封装，主要用于在异步环境中启动后台任务并追踪状态。主要功能包括：
- 用 `AnyIOTaskResult` 记录任务状态/结果/异常
- 用 `AnyIOBackend` 启动任务并维护任务字典

关键组件：
- `AnyIOTaskResult`：任务结果对象
- `AnyIOBackend`：任务后端实现

设计背景：在无外部队列时需要轻量异步任务后端
注意事项：任务 ID 由对象内存地址生成，不保证跨进程稳定
"""

from __future__ import annotations

import traceback
from typing import TYPE_CHECKING, Any

import anyio

from langflow.services.task.backends.base import TaskBackend

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import TracebackType


class AnyIOTaskResult:
    """`AnyIO` 任务结果对象。"""

    def __init__(self) -> None:
        self._status = "PENDING"
        self._result = None
        self._exception: Exception | None = None
        self._traceback: TracebackType | None = None
        self.cancel_scope: anyio.CancelScope | None = None

    @property
    def status(self) -> str:
        """返回任务状态字符串。"""
        if self._status == "DONE":
            return "FAILURE" if self._exception is not None else "SUCCESS"
        return self._status

    @property
    def traceback(self) -> str:
        """返回异常堆栈文本。"""
        if self._traceback is not None:
            return "".join(traceback.format_tb(self._traceback))
        return ""

    @property
    def result(self) -> Any:
        """返回任务结果。"""
        return self._result

    def ready(self) -> bool:
        """判断任务是否完成。"""
        return self._status == "DONE"

    async def run(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        """执行任务函数并记录结果/异常。

        契约：输入可调用对象与参数；完成后更新状态为 `DONE`。
        失败语义：捕获异常并保存 `traceback`，不向外抛出。
        """
        try:
            async with anyio.CancelScope() as scope:
                self.cancel_scope = scope
                self._result = await func(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            self._exception = e
            self._traceback = e.__traceback__
        finally:
            self._status = "DONE"


class AnyIOBackend(TaskBackend):
    """基于 `AnyIO` 的任务后端。"""

    name = "anyio"

    def __init__(self) -> None:
        """初始化任务后端。"""
        self.tasks: dict[str, AnyIOTaskResult] = {}
        self._run_tasks: list[anyio.TaskGroup] = []

    async def launch_task(
        self, task_func: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> tuple[str, AnyIOTaskResult]:
        """启动异步任务并返回任务 ID 与结果对象。

        契约：输入任务函数与参数；输出 `(task_id, task_result)`。
        关键路径（三步）：
        1) 构造 `AnyIOTaskResult` 并生成任务 ID
        2) 使用 `TaskGroup` 启动后台任务
        3) 记录任务结果对象
        失败语义：启动失败时抛 `RuntimeError`。
        """
        try:
            task_result = AnyIOTaskResult()
            task_id = str(id(task_result))
            self.tasks[task_id] = task_result
            async with anyio.create_task_group() as tg:
                tg.start_soon(task_result.run, task_func, *args, **kwargs)
                self._run_tasks.append(tg)

        except Exception as e:
            msg = f"Failed to launch task: {e!s}"
            raise RuntimeError(msg) from e
        return task_id, task_result

    def get_task(self, task_id: str) -> AnyIOTaskResult | None:
        """按任务 ID 获取任务结果对象。"""
        return self.tasks.get(task_id)

    async def cleanup_task(self, task_id: str) -> None:
        """清理已完成任务并释放资源。"""
        if task := self.tasks.get(task_id):
            if task.cancel_scope:
                task.cancel_scope.cancel()
            self.tasks.pop(task_id, None)
