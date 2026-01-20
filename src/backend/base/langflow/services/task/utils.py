"""
模块名称：任务服务辅助工具

本模块提供任务后端相关的辅助函数，主要用于查询 `Celery` worker 状态。主要功能包括：
- 获取 worker 可用性与任务列表

关键组件：
- `get_celery_worker_status`：查询 `Celery` worker 运行状态

设计背景：便于运维与诊断任务队列
注意事项：依赖 `Celery` 控制接口，失败时由调用方处理
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import contextlib

    with contextlib.suppress(ImportError):
        from celery import Celery


def get_celery_worker_status(app: "Celery"):
    """获取 `Celery` worker 运行状态。

    契约：输入 `Celery` 应用实例；输出包含 `availability`/`stats`/`registered_tasks`/`active_tasks`/`scheduled_tasks` 的字典。
    失败语义：通信失败时由 `Celery` 抛出异常。
    """
    i = app.control.inspect()
    availability = app.control.ping()
    stats = i.stats()
    registered_tasks = i.registered()
    active_tasks = i.active()
    scheduled_tasks = i.scheduled()
    return {
        "availability": availability,
        "stats": stats,
        "registered_tasks": registered_tasks,
        "active_tasks": active_tasks,
        "scheduled_tasks": scheduled_tasks,
    }
