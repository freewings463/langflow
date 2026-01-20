"""日志模块入口。

本模块导出日志配置函数与全局 logger 实例。
"""

from lfx.log.logger import configure, logger

__all__ = ["configure", "logger"]
