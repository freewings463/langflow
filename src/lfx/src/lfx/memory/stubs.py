"""
模块名称：lfx Memory Stub 实现

本模块提供与 langflow.memory 接口对齐的 stub 实现，用于独立运行场景。
主要功能：
- 提供消息的存取/更新/删除接口；
- 兼容同步/异步调用模式。

设计背景：在缺少 Langflow 完整实现时保持接口可用。
注意事项：当前实现基于 NoopSession，默认不持久化。
"""

from uuid import UUID

from lfx.log.logger import logger
from lfx.schema.message import Message
from lfx.services.deps import session_scope
from lfx.utils.async_helpers import run_until_complete


async def astore_message(
    message: Message,
    flow_id: str | UUID | None = None,
) -> list[Message]:
    """存储单条消息（异步）

    契约：返回包含已存储消息的列表；缺少必要字段时抛 `ValueError`。
    关键路径：1) 校验消息 2) 补充 flow_id 3) 写入会话并提交。
    异常流：写入失败回滚并抛异常。
    """
    if not message:
        logger.warning("No message provided.")
        return []

    if not message.session_id or not message.sender or not message.sender_name:
        msg = (
            f"All of session_id, sender, and sender_name must be provided. Session ID: {message.session_id},"
            f" Sender: {message.sender}, Sender Name: {message.sender_name}"
        )
        raise ValueError(msg)

    # 注意：flow_id 可由外部传入，用于关联流程。
    if flow_id:
        if isinstance(flow_id, str):
            flow_id = UUID(flow_id)
        message.flow_id = str(flow_id)

    # 注意：当前为简化 stub，不持久化到数据库。
    async with session_scope() as session:
        # 注意：NoopSession 仅保持接口一致性。
        try:
            # 注意：若缺少 id，则生成唯一标识。
            if not hasattr(message, "id") or not message.id:
                try:
                    import nanoid

                    message.id = nanoid.generate()
                except ImportError:
                    # 注意：nanoid 不可用时回退 uuid。
                    import uuid

                    message.id = str(uuid.uuid4())

            await session.add(message)
            await session.commit()
            logger.debug(f"Message stored with ID: {message.id}")
        except Exception as e:
            logger.exception(f"Error storing message: {e}")
            await session.rollback()
            raise
        return [message]


def store_message(
    message: Message,
    flow_id: str | UUID | None = None,
) -> list[Message]:
    """同步存储消息（已弃用）

    契约：调用 `astore_message` 并返回结果。
    注意：请优先使用 `astore_message`。
    """
    return run_until_complete(astore_message(message, flow_id=flow_id))


async def aupdate_messages(messages: Message | list[Message]) -> list[Message]:
    """更新已存储消息（异步）

    契约：返回更新后的消息列表；消息无 id 时抛 `ValueError`。
    关键路径：1) 归一化为列表 2) 校验 id 3) 提交更新。
    """
    if not isinstance(messages, list):
        messages = [messages]

    async with session_scope() as session:
        updated_messages: list[Message] = []
        for message in messages:
            try:
                # 注意：stub 模式仅校验 id 并模拟更新。
                if not hasattr(message, "id") or not message.id:
                    error_message = f"Message without ID cannot be updated: {message}"
                    logger.warning(error_message)
                    raise ValueError(error_message)

                # 注意：UUID 需转换为字符串以保持一致性。
                if message.flow_id and isinstance(message.flow_id, UUID):
                    message.flow_id = str(message.flow_id)

                await session.add(message)
                await session.commit()
                await session.refresh(message)
                updated_messages.append(message)
                logger.debug(f"Message updated: {message.id}")
            except Exception as e:
                logger.exception(f"Error updating message: {e}")
                await session.rollback()
                msg = f"Failed to update message: {e}"
                logger.error(msg)
                raise ValueError(msg) from e

        return updated_messages


async def delete_message(id_: str) -> None:
    """删除单条消息（异步）

    契约：执行删除；stub 模式为 no-op。
    """
    async with session_scope() as session:
        try:
            # 注意：stub 模式不执行真实删除。
            await session.delete(id_)
            await session.commit()
            logger.debug(f"Message deleted: {id_}")
        except Exception as e:
            logger.exception(f"Error deleting message: {e}")
            raise


async def aget_messages(
    sender: str | None = None,  # noqa: ARG001
    sender_name: str | None = None,  # noqa: ARG001
    session_id: str | UUID | None = None,  # noqa: ARG001
    context_id: str | UUID | None = None,  # noqa: ARG001
    order_by: str | None = "timestamp",  # noqa: ARG001
    order: str | None = "DESC",  # noqa: ARG001
    flow_id: UUID | None = None,  # noqa: ARG001
    limit: int | None = None,  # noqa: ARG001
) -> list[Message]:
    """检索消息列表（异步）

    契约：返回消息列表；stub 模式默认返回空列表。
    关键路径：1) 进入会话 2) 执行查询 3) 返回结果。
    """
    async with session_scope() as session:
        try:
            # 注意：stub 模式不查询数据库，返回空列表。
            result = await session.query()  # 注意：NoopSession 默认返回空列表。
            logger.debug(f"Retrieved {len(result)} messages")
        except Exception as e:  # noqa: BLE001
            logger.exception(f"Error retrieving messages: {e}")
            return []
        return result


def get_messages(
    sender: str | None = None,
    sender_name: str | None = None,
    session_id: str | UUID | None = None,
    context_id: str | UUID | None = None,
    order_by: str | None = "timestamp",
    order: str | None = "DESC",
    flow_id: UUID | None = None,
    limit: int | None = None,
) -> list[Message]:
    """同步检索消息（已弃用）

    注意：请优先使用 `aget_messages`。
    """
    return run_until_complete(
        aget_messages(
            sender,
            sender_name,
            session_id,
            context_id,
            order_by,
            order,
            flow_id,
            limit,
        )
    )


async def adelete_messages(session_id: str | None = None, context_id: str | None = None) -> None:
    """按 session/context 删除消息（异步）

    契约：session_id 或 context_id 必须至少提供一个。
    """
    if not session_id and not context_id:
        msg = "Either session_id or context_id must be provided to delete messages."
        raise ValueError(msg)

    async with session_scope() as session:
        try:
            # 注意：stub 模式不执行真实删除。
            await session.delete(session_id or context_id)  # type: ignore  # noqa: PGH003
            await session.commit()
            logger.debug(f"Messages deleted for session: {session_id or context_id}")
        except Exception as e:
            logger.exception(f"Error deleting messages: {e}")
            raise


def delete_messages(session_id: str | None = None, context_id: str | None = None) -> None:
    """同步删除消息（已弃用）

    注意：请优先使用 `adelete_messages`。
    """
    return run_until_complete(adelete_messages(session_id, context_id))


async def aadd_messages(messages: Message | list[Message]) -> list[Message]:
    """批量添加消息（异步）

    契约：返回已添加消息列表。
    关键路径：1) 归一化列表 2) 逐条调用 astore_message。
    """
    if not isinstance(messages, list):
        messages = [messages]

    result = []
    for message in messages:
        stored = await astore_message(message)
        result.extend(stored)
    return result


def add_messages(messages: Message | list[Message]) -> list[Message]:
    """批量添加消息（同步）

    注意：同步包装 `aadd_messages`。
    """
    return run_until_complete(aadd_messages(messages))


async def aadd_messagetables(messages: Message | list[Message]) -> list[Message]:
    """批量添加消息表（别名）

    注意：为兼容历史接口，等价于 `aadd_messages`。
    """
    return await aadd_messages(messages)
