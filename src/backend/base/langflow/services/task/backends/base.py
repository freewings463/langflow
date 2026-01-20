"""
模块名称：任务后端抽象基类

本模块定义任务后端的最小契约，主要用于统一任务启动与任务查询接口。主要功能包括：
- 抽象 `launch_task` 与 `get_task` 接口

关键组件：
- `TaskBackend`：任务后端基类

设计背景：屏蔽不同后端（`AnyIO`/`Celery`）的调用差异
注意事项：具体实现需自行约束 `task_id` 的生成与生命周期
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any


class TaskBackend(ABC):
    """任务后端抽象基类。"""

    name: str

    @abstractmethod
    def launch_task(self, task_func: Callable[..., Any], *args: Any, **kwargs: Any):
        """启动任务并返回任务句柄。

        契约：输入可调用对象与参数；输出任务标识或结果对象。
        失败语义：实现可抛出运行时异常以标识启动失败。
        """

    @abstractmethod
    def get_task(self, task_id: str) -> Any:
        """按任务 ID 获取任务句柄或状态。"""
