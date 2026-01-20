"""
模块名称：`Celery` 任务后端

本模块提供基于 `Celery` 的任务后端实现，主要用于在分布式队列中启动任务并获取结果。主要功能包括：
- 通过 `delay` 调用提交任务
- 通过 `AsyncResult` 获取任务状态

关键组件：
- `CeleryBackend`：任务后端实现

设计背景：生产环境需要可扩展的任务队列
注意事项：`task_func` 必须提供 `delay` 方法
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from celery.result import AsyncResult

from langflow.services.task.backends.base import TaskBackend
from langflow.worker import celery_app

if TYPE_CHECKING:
    from celery import Task


class CeleryBackend(TaskBackend):
    """基于 `Celery` 的任务后端。"""

    name = "celery"

    def __init__(self) -> None:
        self.celery_app = celery_app

    def launch_task(self, task_func: Callable[..., Any], *args: Any, **kwargs: Any) -> tuple[str, AsyncResult]:
        """启动 `Celery` 任务并返回任务 ID 与结果对象。

        契约：输入带 `delay` 方法的任务函数；输出 `(task_id, AsyncResult)`。
        失败语义：`task_func` 缺少 `delay` 时抛 `ValueError`。
        """
        if not hasattr(task_func, "delay"):
            msg = f"Task function {task_func} does not have a delay method"
            raise ValueError(msg)
        task: Task = task_func.delay(*args, **kwargs)
        return task.id, AsyncResult(task.id, app=self.celery_app)

    def get_task(self, task_id: str) -> Any:
        """按任务 ID 获取 `AsyncResult`。"""
        return AsyncResult(task_id, app=self.celery_app)
