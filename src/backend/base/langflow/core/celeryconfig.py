"""
模块名称：Celery 运行时配置

本模块提供 Celery 的 broker、backend 与序列化白名单配置。
主要功能包括：
- 读取环境变量并生成 broker/backend 连接串
- 允许任务内容的序列化格式

关键组件：
- `broker_url` / `result_backend`
- `accept_content`

设计背景：部署环境可能使用 Redis 或 RabbitMQ，需要通过环境变量切换。
注意事项：未配置 Redis 时将回退到 RabbitMQ/本地 Redis 默认值。
"""

import os

langflow_redis_host = os.environ.get("LANGFLOW_REDIS_HOST")
langflow_redis_port = os.environ.get("LANGFLOW_REDIS_PORT")

if langflow_redis_host and langflow_redis_port:
    broker_url = f"redis://{langflow_redis_host}:{langflow_redis_port}/0"
    result_backend = f"redis://{langflow_redis_host}:{langflow_redis_port}/0"
else:
    mq_user = os.environ.get("RABBITMQ_DEFAULT_USER", "langflow")
    mq_password = os.environ.get("RABBITMQ_DEFAULT_PASS", "langflow")
    broker_url = os.environ.get("BROKER_URL", f"amqp://{mq_user}:{mq_password}@localhost:5672//")
    result_backend = os.environ.get("RESULT_BACKEND", "redis://localhost:6379/0")

# 决策：优先使用 Redis 作为 broker/backend
# 问题：部署环境可能没有 RabbitMQ 或配置不一致
# 方案：检测 `LANGFLOW_REDIS_HOST/PORT` 切换到 Redis
# 代价：需要维护两套连接参数
# 重评：当基础设施统一为单一消息系统时移除回退逻辑

# 安全：允许 `pickle` 仅在可信队列场景使用，否则存在反序列化风险
accept_content = ["json", "pickle"]
