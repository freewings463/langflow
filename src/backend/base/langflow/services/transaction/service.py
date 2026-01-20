"""
模块名称：事务日志服务

本模块实现组件执行事务的日志记录，主要用于审计与排障。主要功能包括：
- 在启用开关时将组件执行记录写入数据库
- 支持记录输入、输出、目标节点与错误信息

关键组件：
- TransactionService

设计背景：需要保留构建过程的可追溯记录，以支持审计与问题定位。
注意事项：服务是否启用由 `transactions_storage_enabled` 控制。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from lfx.log.logger import logger
from lfx.services.deps import session_scope
from lfx.services.interfaces import TransactionServiceProtocol

from langflow.services.base import Service
from langflow.services.database.models.transactions.crud import log_transaction as crud_log_transaction
from langflow.services.database.models.transactions.model import TransactionBase

if TYPE_CHECKING:
    from langflow.services.settings.service import SettingsService


class TransactionService(Service, TransactionServiceProtocol):
    """事务日志服务实现。

    契约：仅在 `transactions_storage_enabled=True` 时写入数据库。
    副作用：写入数据库事务表；失败会降级为日志记录。
    失败语义：数据库写入异常被捕获并记录 `Error logging transaction`。
    """

    name = "transaction_service"

    def __init__(self, settings_service: SettingsService):
        """初始化事务服务。

        契约：持有 `settings_service` 用于读取启用开关。
        """
        self.settings_service = settings_service

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
        """记录单次顶点执行事务。

        契约：`flow_id` 为字符串或 `UUID`；成功时无返回。
        关键路径（三步）：
        1) 校验事务开关并构建 `TransactionBase`。
        2) 通过 `session_scope` 写入数据库。
        3) 捕获异常并记录调试日志。
        失败语义：写入失败不会抛出，转为 `logger.debug`，避免影响主流程。
        """
        if not self.is_enabled():
            return

        try:
            flow_uuid = UUID(flow_id) if isinstance(flow_id, str) else flow_id

            transaction = TransactionBase(
                vertex_id=vertex_id,
                target_id=target_id,
                inputs=inputs,
                outputs=outputs,
                status=status,
                error=error,
                flow_id=flow_uuid,
            )

            async with session_scope() as session:
                await crud_log_transaction(session, transaction)

        except Exception as exc:  # noqa: BLE001
            logger.debug(f"Error logging transaction: {exc!s}")

    def is_enabled(self) -> bool:
        """判断事务日志是否启用。

        契约：返回布尔值；缺省时为 `False`。
        """
        return getattr(self.settings_service.settings, "transactions_storage_enabled", False)
