"""
模块名称：Celery 应用构建器

本模块负责创建 Langflow 的 Celery 实例并加载统一配置。
主要功能包括：
- 按配置路径初始化 Celery
- 为 Langflow 任务设置固定队列路由

关键组件：
- `make_celery`：创建并配置 Celery
- `celery_app`：默认应用实例

设计背景：任务路由与配置需要在进程启动时统一注入。
注意事项：配置路径错误会导致 Celery 启动期异常。
"""

from celery import Celery


def make_celery(app_name: str, config: str) -> Celery:
    """创建并配置 Celery 应用实例。

    契约：`config` 为可导入的配置路径字符串，返回 `Celery` 实例。
    副作用：加载配置并写入任务路由。
    失败语义：配置不可导入或缺失项时，Celery 启动期抛异常。

    决策：Langflow 任务统一路由到 `langflow` 队列
    问题：多队列下需要隔离 Langflow 任务
    方案：为 `langflow.worker.tasks.*` 设置固定队列
    代价：缺少按任务类型细分队列的灵活性
    重评：当任务类型增多或需要优先级时引入细分队列
    """
    celery_app = Celery(app_name)
    celery_app.config_from_object(config)
    celery_app.conf.task_routes = {"langflow.worker.tasks.*": {"queue": "langflow"}}
    return celery_app


celery_app = make_celery("langflow", "langflow.core.celeryconfig")
