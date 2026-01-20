"""
模块名称：限制后台日志任务数量

本模块提供带限流策略的 `BackgroundTasks` 子类，主要用于控制构建日志任务数量。主要功能包括：
- 按 `vertex_id` 限制 `log_vertex_build` 任务数量
- 超限时丢弃最旧任务，保留最新日志

关键组件：
- LimitVertexBuildBackgroundTasks：带限制策略的任务队列

设计背景：构建频率高时日志任务可能堆积，需限制以保护队列与内存。
注意事项：仅影响 `log_vertex_build`，不会限制其他后台任务。
"""

from fastapi import BackgroundTasks
from lfx.graph.utils import log_vertex_build

from langflow.services.deps import get_settings_service

class LimitVertexBuildBackgroundTasks(BackgroundTasks):
    """限制单个顶点的构建日志任务数量。

    契约：仅对 `log_vertex_build` 生效；同一 `vertex_id` 超过上限时移除最旧任务。
    副作用：直接修改 `self.tasks` 列表。
    失败语义：无显式异常；配置读取异常将向上抛出。
    """

    def add_task(self, func, *args, **kwargs):
        """添加任务并应用顶点级限制。

        契约：与 FastAPI `BackgroundTasks.add_task` 一致，额外应用 `log_vertex_build` 限制。
        副作用：可能移除最旧的同 `vertex_id` 任务。
        失败语义：配置读取异常向上抛出。
        """
        if func == log_vertex_build:
            vertex_id = kwargs.get("vertex_id")
            if vertex_id is not None:
                relevant_tasks = [
                    t for t in self.tasks if t.func == log_vertex_build and t.kwargs.get("vertex_id") == vertex_id
                ]
                if len(relevant_tasks) >= get_settings_service().settings.max_vertex_builds_per_vertex:
                    oldest_task = relevant_tasks[0]
                    self.tasks.remove(oldest_task)

        super().add_task(func, *args, **kwargs)
