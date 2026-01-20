"""
模块名称：交易日志数据访问

本模块提供交易日志的查询、写入与视图转换。
主要功能包括：按流程查询、限制日志数量、转换读取与日志视图模型。

关键组件：`log_transaction` / `get_transactions_by_flow_id`
设计背景：统一日志存取逻辑，控制数据库增长。
使用场景：执行日志记录、历史回放与监控视图。
注意事项：超过上限会删除最旧记录。
"""

from uuid import UUID

from lfx.log.logger import logger
from sqlmodel import col, delete, select
from sqlmodel.ext.asyncio.session import AsyncSession

from langflow.services.database.models.transactions.model import (
    TransactionBase,
    TransactionLogsResponse,
    TransactionReadResponse,
    TransactionTable,
)
from langflow.services.deps import get_settings_service


async def get_transactions_by_flow_id(
    db: AsyncSession, flow_id: UUID, limit: int | None = 1000
) -> list[TransactionTable]:
    """按流程 `ID` 获取交易日志列表。

    契约：
    - 输入：`db`、`flow_id` 与 `limit`。
    - 输出：`TransactionTable` 列表。
    - 副作用：读取数据库。
    - 失败语义：查询异常透传。

    关键路径：按 `timestamp` 排序并限制数量。

    决策：默认限制 1000 条记录。
    问题：无上限可能导致查询过大。
    方案：提供可选 `limit` 参数。
    代价：默认限制可能截断历史记录。
    重评：当分页接口完善后移除固定上限。
    """
    stmt = (
        select(TransactionTable)
        .where(TransactionTable.flow_id == flow_id)
        .order_by(col(TransactionTable.timestamp))
        .limit(limit)
    )

    transactions = await db.exec(stmt)
    return list(transactions)


async def log_transaction(db: AsyncSession, transaction: TransactionBase) -> TransactionTable | None:
    """记录交易日志并限制最大数量。

    契约：
    - 输入：`db` 与 `transaction`。
    - 输出：新建 `TransactionTable` 或 `None`（无 `flow_id`）。
    - 副作用：写入数据库并删除超限旧记录。
    - 失败语义：写入失败时回滚并抛异常。

    关键路径（三步）：
    1) 校验 `flow_id` 并构造表模型。
    2) 根据配置删除超限旧记录。
    3) 写入新记录并提交事务。

    决策：按 `max_transactions_to_keep` 控制日志数量。
    问题：无限增长会导致数据库膨胀。
    方案：超出上限时删除最旧记录。
    代价：历史日志被裁剪。
    重评：当有归档策略时改为异步归档。
    """
    if not transaction.flow_id:
        await logger.adebug("Transaction flow_id is None")
        return None
    table = TransactionTable(**transaction.model_dump())

    try:
        # 注意：读取最大保留条数配置。
        max_entries = get_settings_service().settings.max_transactions_to_keep

        # 注意：在同一事务中删除超限旧记录。
        delete_older = delete(TransactionTable).where(
            TransactionTable.flow_id == transaction.flow_id,
            col(TransactionTable.id).in_(
                select(TransactionTable.id)
                .where(TransactionTable.flow_id == transaction.flow_id)
                .order_by(col(TransactionTable.timestamp).desc())
                .offset(max_entries - 1)  # 注意：保留最新 `max_entries-1` 加上当前新增。
            ),
        )

        # 注意：同一事务内插入新记录并执行删除。
        db.add(table)
        await db.exec(delete_older)
        await db.commit()

    except Exception:
        await db.rollback()
        raise
    return table


def transform_transaction_table(
    transaction: list[TransactionTable] | TransactionTable,
) -> list[TransactionReadResponse] | TransactionReadResponse:
    """将交易记录转换为读取响应模型。

    契约：
    - 输入：`TransactionTable` 或其列表。
    - 输出：`TransactionReadResponse` 或列表。
    - 副作用：无。
    - 失败语义：模型校验失败抛异常。

    决策：支持单条与列表两种输入。
    问题：调用方可能传入单条或批量结果。
    方案：根据输入类型分支处理。
    代价：返回类型为联合类型。
    重评：当统一返回列表时移除单条分支。
    """
    if isinstance(transaction, list):
        return [TransactionReadResponse.model_validate(t, from_attributes=True) for t in transaction]
    return TransactionReadResponse.model_validate(transaction, from_attributes=True)


def transform_transaction_table_for_logs(
    transaction: list[TransactionTable] | TransactionTable,
) -> list[TransactionLogsResponse] | TransactionLogsResponse:
    """转换为日志视图响应模型（不含 `error` 与 `flow_id`）。"""
    if isinstance(transaction, list):
        return [TransactionLogsResponse.model_validate(t, from_attributes=True) for t in transaction]
    return TransactionLogsResponse.model_validate(transaction, from_attributes=True)
