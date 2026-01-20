"""
模块名称：日志兼容导出

本模块将 `lfx.log.logger` 的方法平铺为模块级函数，兼容旧的导入路径。
主要功能：
- 暴露同步/异步日志方法别名
- 保留原有 `configure` 与 `logger` 接口
设计背景：减少迁移成本，避免历史代码改动。
注意事项：仅做别名导出，不改变日志行为。
"""

from lfx.log.logger import configure, logger

# 迁移上下文：为旧代码保留模块级方法入口。
info = logger.info
debug = logger.debug
warning = logger.warning
error = logger.error
critical = logger.critical
exception = logger.exception

# 迁移上下文：异步日志方法保持一致命名。
aerror = logger.aerror
ainfo = logger.ainfo
adebug = logger.adebug
awarning = logger.awarning
acritical = logger.acritical
aexception = logger.aexception

__all__ = [
    "acritical",
    "adebug",
    "aerror",
    "aexception",
    "ainfo",
    "awarning",
    "configure",
    "critical",
    "debug",
    "error",
    "exception",
    "info",
    "logger",
    "warning",
]
