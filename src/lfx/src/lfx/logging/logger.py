"""模块名称：logging.logger 兼容入口

本模块用于 `lfx.logging.logger` 的向后兼容导出，实际实现已迁移至 `lfx.log.logger`。
主要功能包括：重新导出日志配置、拦截器与服务器集成函数。

关键组件：
- `InterceptHandler/LogConfig`：日志拦截与配置结构
- `configure/logger`：日志配置入口与实例
- `setup_gunicorn_logger/setup_uvicorn_logger`：服务器日志初始化

设计背景：保持旧导入路径稳定，避免升级破坏。
注意事项：本模块仅提供 re-export，不新增逻辑。
"""

# 向后兼容：保持原有导出项
from lfx.log.logger import (
    InterceptHandler,
    LogConfig,
    configure,
    logger,
    setup_gunicorn_logger,
    setup_uvicorn_logger,
)

__all__ = [
    "InterceptHandler",
    "LogConfig",
    "configure",
    "logger",
    "setup_gunicorn_logger",
    "setup_uvicorn_logger",
]
