"""
模块名称：services.transaction.service

本模块提供事务服务的实现，目前包含独立模式下的无操作实现。
主要功能包括：
- 提供满足 TransactionServiceProtocol 的空实现
- 在无事务系统时安全降级

关键组件：
- NoopTransactionService：无操作事务服务

设计背景：lfx 可在无 Langflow 环境下运行，需要替代实现避免调用失败。
注意事项：该实现不会记录或持久化任何事务数据。
"""

from __future__ import annotations

from typing import Any

from lfx.services.interfaces import TransactionServiceProtocol


class NoopTransactionService(TransactionServiceProtocol):
    """独立模式下的无操作事务服务。

    契约：
    - 输入：事务相关参数（见 `log_transaction`）
    - 输出：无
    - 副作用：无
    - 失败语义：不会抛出异常，始终静默返回
    """

    async def log_transaction(
        self,
        flow_id: str,
        vertex_id: str,
        inputs: dict[str, Any] | None,
        outputs: dict[str, Any] | None,
        status: str,
        target_id: str | None = None,
        error: str | None = None,
    ) -> None:
        """记录事务的空实现。

        关键路径：
        1) 接收事务参数但不做持久化
        2) 保持接口稳定，调用方无需分支判断

        失败语义：不抛异常，不记录数据。
        """

    def is_enabled(self) -> bool:
        """返回事务记录是否启用（始终 False）。"""
        return False
