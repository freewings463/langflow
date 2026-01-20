"""模块名称：消息存储与检索模块

本模块提供消息的存储、检索、更新和删除功能，主要用于管理对话历史和消息记录。
主要功能包括：
- 消息的增删改查操作
- 对话历史管理
- 异步和同步的消息处理接口
- 与数据库交互的消息存储功能

设计背景：这是Langflow的消息管理核心，支持会话、上下文和流程级别的消息组织
注意事项：需要正确处理UUID转换、错误消息过滤和数据库事务管理
"""

import asyncio
import json
from collections.abc import Sequence
from uuid import UUID

from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.messages import BaseMessage
from lfx.log.logger import logger
from lfx.utils.async_helpers import run_until_complete
from sqlalchemy import delete
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from langflow.schema.message import Message
from langflow.services.database.models.message.model import MessageRead, MessageTable
from langflow.services.deps import session_scope


def _get_variable_query(
    sender: str | None = None,
    sender_name: str | None = None,
    session_id: str | UUID | None = None,
    context_id: str | None = None,
    order_by: str | None = "timestamp",
    order: str | None = "DESC",
    flow_id: UUID | None = None,
    limit: int | None = None,
):
    """构建动态消息查询语句
    
    决策：使用SQLModel构建动态查询
    问题：需要根据不同的过滤条件构建不同的查询语句
    方案：使用链式调用逐步添加WHERE条件
    代价：查询复杂度随过滤条件增加而增加
    重评：当查询性能成为瓶颈时需要重新评估索引策略
    
    关键路径（三步）：
    1) 创建基础查询语句（排除错误消息）
    2) 根据提供的参数添加WHERE条件
    3) 添加排序和限制条件
    
    异常流：无特殊异常处理
    性能瓶颈：查询条件过多可能导致性能下降
    排障入口：无特定日志关键字
    """
    stmt = select(MessageTable).where(MessageTable.error == False)  # noqa: E712
    if sender:
        stmt = stmt.where(MessageTable.sender == sender)
    if sender_name:
        stmt = stmt.where(MessageTable.sender_name == sender_name)
    if session_id:
        stmt = stmt.where(MessageTable.session_id == session_id)
    if context_id:
        stmt = stmt.where(MessageTable.context_id == context_id)
    if flow_id:
        stmt = stmt.where(MessageTable.flow_id == flow_id)
    if order_by:
        col_attr = getattr(MessageTable, order_by).desc() if order == "DESC" else getattr(MessageTable, order_by).asc()
        stmt = stmt.order_by(col_attr)
    if limit:
        stmt = stmt.limit(limit)
    return stmt


def get_messages(
    sender: str | None = None,
    sender_name: str | None = None,
    session_id: str | UUID | None = None,
    context_id: str | None = None,
    order_by: str | None = "timestamp",
    order: str | None = "DESC",
    flow_id: UUID | None = None,
    limit: int | None = None,
) -> list[Message]:
    """【已弃用】根据提供的过滤器从监控服务检索消息
    
    注意：使用`aget_messages`异步版本替代此函数
    
    参数说明：
        sender (Optional[str]): 消息发送者（例如，"Machine"或"User"）
        sender_name (Optional[str]): 发送者名称
        session_id (Optional[str]): 与消息关联的会话ID
        context_id (Optional[str]): 与消息关联的上下文ID
        order_by (Optional[str]): 用于排序的字段，默认为"timestamp"
        order (Optional[str]): 检索消息的顺序，默认为"DESC"
        flow_id (Optional[UUID]): 与消息关联的流程ID
        limit (Optional[int]): 检索消息的最大数量
    
    返回：
        List[Message]: 表示检索到消息的Message对象列表
    
    关键路径（三步）：
    1) 将同步调用转换为异步调用
    2) 执行异步消息检索
    3) 返回结果
    
    异常流：使用run_until_complete将异步函数转换为同步调用
    性能瓶颈：同步调用可能阻塞事件循环
    排障入口：无特定日志关键字
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


async def aget_messages(
    sender: str | None = None,
    sender_name: str | None = None,
    session_id: str | UUID | None = None,
    context_id: str | None = None,
    order_by: str | None = "timestamp",
    order: str | None = "DESC",
    flow_id: UUID | None = None,
    limit: int | None = None,
) -> list[Message]:
    """根据提供的过滤器从监控服务检索消息
    
    参数说明：
        sender (Optional[str]): 消息发送者（例如，"Machine"或"User"）
        sender_name (Optional[str]): 发送者名称
        session_id (Optional[str]): 与消息关联的会话ID
        context_id (Optional[str]): 与消息关联的上下文ID
        order_by (Optional[str]): 用于排序的字段，默认为"timestamp"
        order (Optional[str]): 检索消息的顺序，默认为"DESC"
        flow_id (Optional[UUID]): 与消息关联的流程ID
        limit (Optional[int]): 检索消息的最大数量
    
    返回：
        List[Message]: 表示检索到消息的Message对象列表
    
    关键路径（三步）：
    1) 构建动态查询语句
    2) 在会话范围内执行查询
    3) 将结果转换为Message对象列表
    
    异常流：无特殊异常处理
    性能瓶颈：大数据量查询可能影响性能
    排障入口：无特定日志关键字
    """
    async with session_scope() as session:
        stmt = _get_variable_query(sender, sender_name, session_id, context_id, order_by, order, flow_id, limit)
        messages = await session.exec(stmt)
        return [await Message.create(**d.model_dump()) for d in messages]


def add_messages(messages: Message | list[Message], flow_id: str | UUID | None = None):
    """【已弃用】向监控服务添加消息
    
    注意：使用`aadd_messages`异步版本替代此函数
    """
    return run_until_complete(aadd_messages(messages, flow_id=flow_id))


async def aadd_messages(messages: Message | list[Message], flow_id: str | UUID | None = None):
    """向监控服务添加消息
    
    关键路径（三步）：
    1) 确保消息参数为列表格式
    2) 验证消息类型有效性
    3) 将消息转换为数据库模型并保存
    
    异常流：类型验证失败时抛出ValueError，其他异常被记录并重新抛出
    性能瓶颈：批量插入大量消息可能影响性能
    排障入口：类型验证错误日志
    """
    if not isinstance(messages, list):
        messages = [messages]

    # 检查所有消息是否为Message实例（来自langflow或lfx）
    for message in messages:
        # 接受来自langflow和lfx包的Message实例
        is_valid_message = isinstance(message, Message) or (
            hasattr(message, "__class__") and message.__class__.__name__ in ["Message", "ErrorMessage"]
        )
        if not is_valid_message:
            types = ", ".join([str(type(msg)) for msg in messages])
            msg = f"The messages must be instances of Message. Found: {types}"
            raise ValueError(msg)

    try:
        messages_models = [MessageTable.from_message(msg, flow_id=flow_id) for msg in messages]
        async with session_scope() as session:
            messages_models = await aadd_messagetables(messages_models, session)
        return [await Message.create(**message.model_dump()) for message in messages_models]
    except Exception as e:
        await logger.aexception(e)
        raise


async def aupdate_messages(messages: Message | list[Message]) -> list[Message]:
    """更新消息
    
    关键路径（三步）：
    1) 确保消息参数为列表格式
    2) 在会话范围内查找并更新每条消息
    3) 将更新后的消息转换为返回格式
    
    异常流：如果消息不存在则抛出ValueError，UUID转换失败时也可能抛出异常
    性能瓶颈：逐条更新大量消息可能影响性能
    排障入口：消息不存在的警告日志
    """
    if not isinstance(messages, list):
        messages = [messages]

    async with session_scope() as session:
        updated_messages: list[MessageTable] = []
        for message in messages:
            msg = await session.get(MessageTable, message.id)
            if msg:
                msg = msg.sqlmodel_update(message.model_dump(exclude_unset=True, exclude_none=True))
                # 如果flow_id是字符串则转换为UUID，防止保存到数据库时出错
                if msg.flow_id and isinstance(msg.flow_id, str):
                    msg.flow_id = UUID(msg.flow_id)
                result = session.add(msg)
                if asyncio.iscoroutine(result):
                    await result
                updated_messages.append(msg)
            else:
                error_message = f"Message with id {message.id} not found"
                await logger.awarning(error_message)
                raise ValueError(error_message)

        return [MessageRead.model_validate(message, from_attributes=True) for message in updated_messages]


async def aadd_messagetables(messages: list[MessageTable], session: AsyncSession, retry_count: int = 0):
    """使用重试逻辑添加消息到数据库以处理CancelledError
    
    决策：实现CancelledError的重试机制
    问题：在build_public_tmp调用时可能出现CancelledError，但在build_flow中不会
    方案：实现最多3次重试的机制防止无限递归
    代价：可能增加操作完成的时间
    重评：当CancelledError的根本原因被解决时可以移除重试逻辑
    
    参数说明：
        messages: 要添加的MessageTable对象列表
        session: 数据库会话
        retry_count: 内部重试计数器（最大3次以防止无限循环）
    
    关键路径（三步）：
    1) 尝试添加消息到数据库并提交事务
    2) 如果发生CancelledError则回滚并重试（最多3次）
    3) 刷新消息并处理JSON属性
    
    异常流：超出重试次数时抛出ValueError，其他异常被记录并重新抛出
    性能瓶颈：重试机制可能增加操作完成时间
    排障入口：重试次数达到上限的警告日志
    """
    max_retries = 3
    try:
        try:
            for message in messages:
                result = session.add(message)
                if asyncio.iscoroutine(result):
                    await result
            await session.commit()
            # 这是一个变通方案
            # 我们这样做是因为build_public_tmp会导致CancelledError被抛出
            # 而build_flow不会
        except asyncio.CancelledError:
            await session.rollback()
            if retry_count >= max_retries:
                await logger.awarning(
                    f"Max retries ({max_retries}) reached for aadd_messagetables due to CancelledError"
                )
                error_msg = "Add Message operation cancelled after multiple retries"
                raise ValueError(error_msg) from None
            return await aadd_messagetables(messages, session, retry_count + 1)
        for message in messages:
            await session.refresh(message)
    except asyncio.CancelledError as e:
        await logger.aexception(e)
        error_msg = "Operation cancelled"
        raise ValueError(error_msg) from e
    except Exception as e:
        await logger.aexception(e)
        raise

    new_messages = []
    for msg in messages:
        msg.properties = json.loads(msg.properties) if isinstance(msg.properties, str) else msg.properties  # type: ignore[arg-type]
        msg.content_blocks = [json.loads(j) if isinstance(j, str) else j for j in msg.content_blocks]  # type: ignore[arg-type]
        msg.category = msg.category or ""
        new_messages.append(msg)

    return [MessageRead.model_validate(message, from_attributes=True) for message in new_messages]


def delete_messages(session_id: str | None = None, context_id: str | None = None) -> None:
    """【已弃用】根据提供的会话ID从监控服务删除消息
    
    注意：使用`adelete_messages`异步版本替代此函数
    
    参数说明：
        session_id (str): 要删除消息关联的会话ID
        context_id (str): 要删除消息关联的上下文ID
    """
    return run_until_complete(adelete_messages(session_id, context_id))


async def adelete_messages(session_id: str | None = None, context_id: str | None = None) -> None:
    """根据提供的会话ID从监控服务删除消息
    
    参数说明：
        session_id (str): 要删除消息关联的会话ID
        context_id (str): 要删除消息关联的上下文ID
    
    关键路径（三步）：
    1) 验证至少提供了一个ID参数
    2) 构建删除语句
    3) 在会话范围内执行删除操作
    
    异常流：未提供ID参数时抛出ValueError
    性能瓶颈：删除大量消息可能影响性能
    排障入口：无ID参数提供的错误消息
    """
    async with session_scope() as session:
        if not session_id and not context_id:
            msg = "Either session_id or context_id must be provided to delete messages."
            raise ValueError(msg)

        # 确定要过滤的字段
        filter_column = MessageTable.context_id if context_id else MessageTable.session_id
        filter_value = context_id if context_id else session_id

        stmt = (
            delete(MessageTable)
            .where(col(filter_column) == filter_value)
            .execution_options(synchronize_session="fetch")
        )
        await session.exec(stmt)


async def delete_message(id_: str) -> None:
    """根据提供的ID从监控服务删除消息
    
    参数说明：
        id_ (str): 要删除的消息ID
    
    关键路径（三步）：
    1) 在会话范围内获取消息
    2) 如果消息存在则删除
    3) 完成事务
    
    异常流：无特殊异常处理
    性能瓶颈：无显著性能瓶颈
    排障入口：无特定日志关键字
    """
    async with session_scope() as session:
        message = await session.get(MessageTable, id_)
        if message:
            await session.delete(message)


def store_message(
    message: Message,
    flow_id: str | UUID | None = None,
) -> list[Message]:
    """【已弃用】在内存中存储消息
    
    注意：使用`astore_message`异步版本替代此函数
    
    参数说明：
        message (Message): 要存储的消息
        flow_id (Optional[str | UUID]): 与消息关联的流程ID
            当从CustomComponent运行时，您可以使用`self.graph.flow_id`访问
    
    返回：
        List[Message]: 包含存储消息的数据列表
    
    抛出：
        ValueError: 如果任何必需参数（session_id、sender、sender_name）未提供
    """
    return run_until_complete(astore_message(message, flow_id=flow_id))


async def astore_message(
    message: Message,
    flow_id: str | UUID | None = None,
) -> list[Message]:
    """在内存中存储消息
    
    参数说明：
        message (Message): 要存储的消息
        flow_id (Optional[str]): 与消息关联的流程ID
            当从CustomComponent运行时，您可以使用`self.graph.flow_id`访问
    
    返回：
        List[Message]: 包含存储消息的数据列表
    
    抛出：
        ValueError: 如果任何必需参数（session_id、sender、sender_name）未提供
    
    关键路径（三步）：
    1) 验证必需参数是否存在
    2) 检查消息是否已存在（根据ID）并决定更新或新增
    3) 执行相应的数据库操作
    
    异常流：缺少必需参数时抛出ValueError
    性能瓶颈：消息验证和数据库操作可能影响性能
    排障入口：缺少必需参数的错误消息
    """
    if not message:
        await logger.awarning("No message provided.")
        return []

    if not message.session_id or not message.sender or not message.sender_name:
        msg = (
            f"All of session_id, sender, and sender_name must be provided. Session ID: {message.session_id},"
            f" Sender: {message.sender}, Sender Name: {message.sender_name}"
        )
        raise ValueError(msg)
    if hasattr(message, "id") and message.id:
        # 如果消息有ID且存在于数据库中，则更新它
        # 否则抛出错误并将消息添加到数据库
        try:
            return await aupdate_messages([message])
        except ValueError as e:
            await logger.aerror(e)
    if flow_id and not isinstance(flow_id, UUID):
        flow_id = UUID(flow_id)
    return await aadd_messages([message], flow_id=flow_id)


class LCBuiltinChatMemory(BaseChatMessageHistory):
    """【已弃用】为向后兼容保留"""
    
    def __init__(
        self,
        flow_id: str,
        session_id: str,
        context_id: str | None = None,
    ) -> None:
        self.flow_id = flow_id
        self.session_id = session_id
        self.context_id = context_id

    @property
    def messages(self) -> list[BaseMessage]:
        """获取同步消息列表
        
        关键路径（三步）：
        1) 使用同步方法检索消息
        2) 过滤掉错误消息
        3) 转换为LangChain消息格式
        
        异常流：无特殊异常处理
        性能瓶颈：同步调用可能阻塞事件循环
        排障入口：无特定日志关键字
        """
        messages = get_messages(
            session_id=self.session_id,
            context_id=self.context_id,
        )
        return [m.to_lc_message() for m in messages if not m.error]  # 排除错误消息

    async def aget_messages(self) -> list[BaseMessage]:
        """获取异步消息列表
        
        关键路径（三步）：
        1) 使用异步方法检索消息
        2) 过滤掉错误消息
        3) 转换为LangChain消息格式
        
        异常流：无特殊异常处理
        性能瓶颈：无显著性能瓶颈
        排障入口：无特定日志关键字
        """
        messages = await aget_messages(
            session_id=self.session_id,
            context_id=self.context_id,
        )
        return [m.to_lc_message() for m in messages if not m.error]  # 排除错误消息

    def add_messages(self, messages: Sequence[BaseMessage]) -> None:
        """添加消息到存储
        
        关键路径（三步）：
        1) 遍历并转换LangChain消息为内部格式
        2) 设置会话和上下文ID
        3) 存储消息
        
        异常流：无特殊异常处理
        性能瓶颈：同步存储可能阻塞事件循环
        排障入口：无特定日志关键字
        """
        for lc_message in messages:
            message = Message.from_lc_message(lc_message)
            message.session_id = self.session_id
            message.context_id = self.context_id
            store_message(message, flow_id=self.flow_id)

    async def aadd_messages(self, messages: Sequence[BaseMessage]) -> None:
        """异步添加消息到存储
        
        关键路径（三步）：
        1) 遍历并转换LangChain消息为内部格式
        2) 设置会话和上下文ID
        3) 异步存储消息
        
        异常流：无特殊异常处理
        性能瓶颈：无显著性能瓶颈
        排障入口：无特定日志关键字
        """
        for lc_message in messages:
            message = Message.from_lc_message(lc_message)
            message.session_id = self.session_id
            message.context_id = self.context_id
            await astore_message(message, flow_id=self.flow_id)

    def clear(self) -> None:
        """清空同步消息存储
        
        关键路径（三步）：
        1) 准备删除参数
        2) 调用同步删除方法
        3) 完成操作
        
        异常流：无特殊异常处理
        性能瓶颈：同步删除可能阻塞事件循环
        排障入口：无特定日志关键字
        """
        delete_messages(self.session_id, self.context_id)

    async def aclear(self) -> None:
        """清空异步消息存储
        
        关键路径（三步）：
        1) 准备删除参数
        2) 调用异步删除方法
        3) 完成操作
        
        异常流：无特殊异常处理
        性能瓶颈：无显著性能瓶颈
        排障入口：无特定日志关键字
        """
        await adelete_messages(self.session_id, self.context_id)