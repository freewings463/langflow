"""
模块名称：作业队列服务工厂

本模块提供 `JobQueueService` 的工厂封装，用于统一服务注册与实例化。主要功能包括：
- 将服务类型绑定到 `ServiceFactory`
- 提供标准化 `create` 实例化入口

关键组件：
- `JobQueueServiceFactory`：作业队列服务创建器

使用场景：服务注册与依赖注入阶段需要统一实例化入口。
设计背景：服务层通过工厂模式解耦实例化与调用方。
注意事项：`create` 每次返回新实例，不做缓存。
"""

from langflow.services.base import Service
from langflow.services.factory import ServiceFactory
from langflow.services.job_queue.service import JobQueueService


class JobQueueServiceFactory(ServiceFactory):
    """作业队列服务工厂。

    契约：`create` 返回新的 `JobQueueService`；无输入参数；失败语义：实例化异常向上抛出。
    关键路径：通过 `create` 生成服务实例并交给服务管理器。
    决策：使用工厂封装服务实例化。
    问题：直接在调用方创建实例导致耦合与测试困难。
    方案：以工厂模式集中实例化逻辑。
    代价：多一层间接调用与样板代码。
    重评：当服务构造逻辑稳定且无需扩展时可简化为直接构造。
    """

    def __init__(self):
        """绑定服务类型到工厂。

        契约：无输入输出；副作用：设置工厂内部的服务类型引用。
        关键路径：调用父类 `__init__` 并传入 `JobQueueService`。
        决策：在初始化阶段绑定类型而非运行时传入。
        问题：运行时传入易造成调用方出错。
        方案：构造时绑定固定类型。
        代价：复用工厂需要新建类。
        重评：当需要动态服务类型时改为参数化工厂。
        """
        super().__init__(JobQueueService)

    def create(self) -> Service:
        """创建新的作业队列服务实例。

        契约：无输入，返回 `JobQueueService` 实例；副作用：仅分配对象，不启动任务；失败语义：实例化失败会抛出异常给调用方。
        关键路径：直接调用 `JobQueueService()` 构造。
        决策：每次 `create` 返回新实例。
        问题：共享单例会引入跨请求状态污染。
        方案：保持无缓存实例化。
        代价：频繁创建可能增加初始化开销。
        重评：当需要复用实例以降低成本时引入缓存。
        """
        return JobQueueService()
