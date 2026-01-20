"""
模块名称：数据库服务导出

模块目的：暴露数据库服务实现供外部使用。
使用场景：在无数据库依赖的运行模式下提供 Noop 服务。
主要功能包括：
- 导出 `NoopDatabaseService`

设计背景：保持 lfx 在离线/轻量场景下可运行。
注意：该模块仅导出无操作实现，不提供真实数据库连接。
"""

from lfx.services.database.service import NoopDatabaseService

__all__ = ["NoopDatabaseService"]
