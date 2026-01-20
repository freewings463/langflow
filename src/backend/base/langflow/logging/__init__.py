"""
模块名称：日志入口聚合

本模块聚合日志配置与开关函数，作为 `langflow.logging` 的对外统一出口。
主要功能：
- 暴露 `configure` 与 `logger`
- 提供日志启停控制函数
设计背景：保持历史导入路径稳定，减少上层模块改动。
注意事项：仅做导出聚合，不包含业务逻辑。
"""

from lfx.log.logger import configure, logger

from .setup import disable_logging, enable_logging

__all__ = ["configure", "disable_logging", "enable_logging", "logger"]
