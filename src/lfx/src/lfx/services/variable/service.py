"""
模块名称：Variable 服务（轻量实现）

本模块提供变量服务的最小实现，支持内存存储与环境变量回退。
主要功能：
- 设置/获取/删除变量（内存）；
- 读取时回退到环境变量。

设计背景：LFX 独立运行场景无需数据库，仅需轻量变量支持。
注意事项：变量不会持久化，进程重启即丢失。
"""

import os

from lfx.log.logger import logger
from lfx.services.base import Service


class VariableService(Service):
    """Minimal variable service with in-memory storage and environment fallback.

    This is a lightweight implementation for LFX that maintains in-memory
    variables and falls back to environment variables for reads. No database storage.
    """

    name = "variable_service"

    def __init__(self) -> None:
        """初始化变量服务

        契约：创建内存字典并标记服务就绪。
        """
        super().__init__()
        self._variables: dict[str, str] = {}
        self.set_ready()
        logger.debug("Variable service initialized (env vars only)")

    def get_variable(self, name: str, **kwargs) -> str | None:  # noqa: ARG002
        """获取变量值

        契约：优先返回内存值，未命中回退环境变量。
        """
        # 注意：优先读内存缓存。
        if name in self._variables:
            return self._variables[name]

        # 注意：未命中时回退环境变量。
        value = os.getenv(name)
        if value:
            logger.debug(f"Variable '{name}' loaded from environment")
        return value

    def set_variable(self, name: str, value: str, **kwargs) -> None:  # noqa: ARG002
        """设置变量值（仅内存）。"""
        self._variables[name] = value
        logger.debug(f"Variable '{name}' set (in-memory only)")

    def delete_variable(self, name: str, **kwargs) -> None:  # noqa: ARG002
        """删除变量（仅内存）。"""
        if name in self._variables:
            del self._variables[name]
            logger.debug(f"Variable '{name}' deleted (from in-memory cache)")

    def list_variables(self, **kwargs) -> list[str]:  # noqa: ARG002
        """列出全部变量名（仅内存）。"""
        return list(self._variables.keys())

    async def teardown(self) -> None:
        """释放变量服务资源。"""
        self._variables.clear()
        logger.debug("Variable service teardown")
