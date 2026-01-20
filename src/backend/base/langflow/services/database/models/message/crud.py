"""
模块名称：消息更新操作

本模块提供消息记录的更新入口。
主要功能包括：异步更新消息与兼容同步调用的包装函数。

关键组件：`_update_message` / `update_message`
设计背景：保留历史同步接口以兼容旧调用路径。
使用场景：消息编辑、错误标记与属性更新。
注意事项：`update_message` 为兼容接口，建议使用异步版本。
"""

from uuid import UUID

from lfx.utils.async_helpers import run_until_complete

from langflow.services.database.models.message.model import MessageTable, MessageUpdate
from langflow.services.deps import session_scope


async def _update_message(message_id: UUID | str, message: MessageUpdate | dict):
    """异步更新消息记录。

    契约：
    - 输入：`message_id` 与 `message`（模型或字典）。
    - 输出：更新后的 `MessageTable`。
    - 副作用：写入数据库并刷新对象。
    - 失败语义：消息不存在时抛 `ValueError`。

    关键路径：
    1) 将输入转换为 `MessageUpdate`。
    2) 查询并校验消息存在性。
    3) 应用更新并持久化。

    决策：更新前执行 `exclude_unset` 与 `exclude_none`。
    问题：部分更新不应覆盖未传字段。
    方案：仅更新显式传入字段。
    代价：无法将字段更新为 `None`。
    重评：当需要显式清空字段时调整排除规则。
    """
    if not isinstance(message, MessageUpdate):
        message = MessageUpdate(**message)
    async with session_scope() as session:
        db_message = await session.get(MessageTable, message_id)
        if not db_message:
            msg = "Message not found"
            raise ValueError(msg)
        message_dict = message.model_dump(exclude_unset=True, exclude_none=True)
        db_message.sqlmodel_update(message_dict)
        session.add(db_message)
        await session.flush()
        await session.refresh(db_message)
        return db_message


def update_message(message_id: UUID | str, message: MessageUpdate | dict):
    """同步包装更新接口（兼容旧调用）。

    契约：
    - 输入：`message_id` 与 `message`。
    - 输出：更新后的 `MessageTable`。
    - 副作用：同步阻塞调用异步更新。
    - 失败语义：异常透传。

    决策：保留同步接口以兼容旧版本调用。
    问题：历史调用路径尚未完全迁移到异步。
    方案：用 `run_until_complete` 包装异步实现。
    代价：阻塞当前线程。
    重评：当所有调用迁移完成后移除该接口。
    """
    return run_until_complete(_update_message(message_id, message))
