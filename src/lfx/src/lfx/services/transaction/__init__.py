"""
模块名称：services.transaction

本模块提供事务服务的包入口，统一暴露无操作实现供独立模式使用。
主要功能包括：
- 导出 NoopTransactionService 以满足接口依赖

关键组件：
- NoopTransactionService：无操作事务服务

设计背景：在未引入完整事务系统时，仍需满足协议以保持依赖稳定。
注意事项：该服务不持久化事务，不产生任何副作用。
"""

from lfx.services.transaction.service import NoopTransactionService

__all__ = ["NoopTransactionService"]
