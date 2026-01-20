"""
模块名称：任务服务

本模块提供任务服务的统一接口，主要用于在不同后端之间切换并启动任务。主要功能包括：
- 选择任务后端并启动任务
- 提供直接执行与异步启动两种调用方式

关键组件：
- `TaskService`：任务服务

设计背景：屏蔽任务后端差异，统一对外调用
注意事项：当前默认使用 `AnyIO` 后端
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from langflow.services.base import Service
from langflow.services.task.backends.anyio import AnyIOBackend

if TYPE_CHECKING:
    from lfx.services.settings.service import SettingsService

    from langflow.services.task.backends.base import TaskBackend


class TaskService(Service):
    """任务服务实现。"""

    name = "task_service"

    def __init__(self, settings_service: SettingsService):
        """初始化任务服务并选择后端。

        契约：输入 `settings_service`；创建后端并缓存配置引用。
        """
        self.settings_service = settings_service
        self.use_celery = False
        self.backend = self.get_backend()

    @property
    def backend_name(self) -> str:
        """返回当前后端名称。"""
        return self.backend.name

    def get_backend(self) -> TaskBackend:
        """选择任务后端实现。

        契约：当前返回 `AnyIOBackend`；可扩展为配置驱动。
        """
        return AnyIOBackend()

    async def launch_and_await_task(
        self,
        task_func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """直接执行任务并等待结果。

        契约：输入可调用对象与参数；输出执行结果；不经过后端调度。
        """
        return await task_func(*args, **kwargs)

    async def launch_task(self, task_func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """通过后端启动任务。

        契约：输入任务函数与参数；输出后端返回的任务结果或句柄。
        失败语义：后端启动失败时抛出其原始异常。
        """
        task = self.backend.launch_task(task_func, *args, **kwargs)
        return await task if isinstance(task, Coroutine) else task
