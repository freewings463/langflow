"""
模块名称：任务服务工厂

本模块提供任务服务的创建工厂，主要用于统一构造 `TaskService`。主要功能包括：
- 生成任务服务实例

关键组件：
- `TaskServiceFactory`：任务服务工厂

设计背景：统一服务创建入口，便于依赖注入
注意事项：当前实现直接返回 `TaskService` 默认实例
"""

from typing_extensions import override

from langflow.services.factory import ServiceFactory
from langflow.services.task.service import TaskService


class TaskServiceFactory(ServiceFactory):
    """任务服务工厂。"""

    def __init__(self) -> None:
        super().__init__(TaskService)

    @override
    def create(self):
        """创建任务服务实例。

        契约：无输入；返回 `TaskService` 实例。
        失败语义：`TaskService` 构造参数不匹配时抛 `TypeError`。
        """
        return TaskService()
