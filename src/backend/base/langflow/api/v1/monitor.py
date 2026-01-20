"""
模块名称：监控与审计接口

本模块提供构建记录、消息记录与交易日志的查询与维护能力。
主要功能：
- 查询/删除顶点构建记录
- 查询/更新/删除消息记录与会话
- 分页获取交易日志
设计背景：为运维与排障提供统一的监控入口。
注意事项：接口需鉴权，异常统一转为 500。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi_pagination import Page, Params
from fastapi_pagination.ext.sqlmodel import apaginate
from sqlalchemy import delete
from sqlmodel import col, select

from langflow.api.utils import DbSession, custom_params
from langflow.schema.message import MessageResponse
from langflow.services.auth.utils import get_current_active_user
from langflow.services.database.models.flow.model import Flow
from langflow.services.database.models.message.model import MessageRead, MessageTable, MessageUpdate
from langflow.services.database.models.transactions.crud import transform_transaction_table_for_logs
from langflow.services.database.models.transactions.model import TransactionLogsResponse, TransactionTable
from langflow.services.database.models.user.model import User
from langflow.services.database.models.vertex_builds.crud import (
    delete_vertex_builds_by_flow_id,
    get_vertex_builds_by_flow_id,
)
from langflow.services.database.models.vertex_builds.model import VertexBuildMapModel

router = APIRouter(prefix="/monitor", tags=["Monitor"])


@router.get("/builds", dependencies=[Depends(get_current_active_user)])
async def get_vertex_builds(flow_id: Annotated[UUID, Query()], session: DbSession) -> VertexBuildMapModel:
    """获取指定流程的顶点构建记录映射。"""
    try:
        vertex_builds = await get_vertex_builds_by_flow_id(session, flow_id)
        return VertexBuildMapModel.from_list_of_dicts(vertex_builds)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/builds", status_code=204, dependencies=[Depends(get_current_active_user)])
async def delete_vertex_builds(flow_id: Annotated[UUID, Query()], session: DbSession) -> None:
    """删除指定流程的顶点构建记录。"""
    try:
        await delete_vertex_builds_by_flow_id(session, flow_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/messages/sessions")
async def get_message_sessions(
    session: DbSession,
    current_user: Annotated[User, Depends(get_current_active_user)],
    flow_id: Annotated[UUID | None, Query()] = None,
) -> list[str]:
    """获取当前用户的消息会话 ID 列表。"""
    try:
        # 性能：使用 JOIN 替代子查询。
        stmt = select(MessageTable.session_id).distinct()
        stmt = stmt.join(Flow, MessageTable.flow_id == Flow.id)
        stmt = stmt.where(col(MessageTable.session_id).isnot(None))
        stmt = stmt.where(Flow.user_id == current_user.id)

        if flow_id:
            stmt = stmt.where(MessageTable.flow_id == flow_id)

        session_ids = await session.exec(stmt)
        return list(session_ids)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/messages")
async def get_messages(
    session: DbSession,
    current_user: Annotated[User, Depends(get_current_active_user)],
    flow_id: Annotated[UUID | None, Query()] = None,
    session_id: Annotated[str | None, Query()] = None,
    sender: Annotated[str | None, Query()] = None,
    sender_name: Annotated[str | None, Query()] = None,
    order_by: Annotated[str | None, Query()] = "timestamp",
) -> list[MessageResponse]:
    """按条件查询消息列表。"""
    try:
        # 性能：使用 JOIN 替代子查询。
        stmt = select(MessageTable)
        stmt = stmt.join(Flow, MessageTable.flow_id == Flow.id)
        stmt = stmt.where(Flow.user_id == current_user.id)

        if flow_id:
            stmt = stmt.where(MessageTable.flow_id == flow_id)
        if session_id:
            from urllib.parse import unquote

            decoded_session_id = unquote(session_id)
            stmt = stmt.where(MessageTable.session_id == decoded_session_id)
        if sender:
            stmt = stmt.where(MessageTable.sender == sender)
        if sender_name:
            stmt = stmt.where(MessageTable.sender_name == sender_name)
        if order_by:
            order_col = getattr(MessageTable, order_by).asc()
            stmt = stmt.order_by(order_col)
        messages = await session.exec(stmt)
        return [MessageResponse.model_validate(d, from_attributes=True) for d in messages]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/messages", status_code=204, dependencies=[Depends(get_current_active_user)])
async def delete_messages(message_ids: list[UUID], session: DbSession) -> None:
    """批量删除消息记录。"""
    try:
        await session.exec(delete(MessageTable).where(MessageTable.id.in_(message_ids)))  # type: ignore[attr-defined]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.put("/messages/{message_id}", dependencies=[Depends(get_current_active_user)], response_model=MessageRead)
async def update_message(
    message_id: UUID,
    message: MessageUpdate,
    session: DbSession,
):
    """更新单条消息内容并标记编辑状态。"""
    try:
        db_message = await session.get(MessageTable, message_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    if not db_message:
        raise HTTPException(status_code=404, detail="Message not found")

    try:
        message_dict = message.model_dump(exclude_unset=True, exclude_none=True)
        if "text" in message_dict and message_dict["text"] != db_message.text:
            message_dict["edit"] = True
        db_message.sqlmodel_update(message_dict)
        session.add(db_message)
        await session.flush()
        await session.refresh(db_message)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return db_message


@router.patch(
    "/messages/session/{old_session_id}",
    dependencies=[Depends(get_current_active_user)],
)
async def update_session_id(
    old_session_id: str,
    new_session_id: Annotated[str, Query(..., description="The new session ID to update to")],
    session: DbSession,
) -> list[MessageResponse]:
    """批量更新会话 ID。"""
    try:
        # 实现：查询旧会话下全部消息。
        stmt = select(MessageTable).where(MessageTable.session_id == old_session_id)
        messages = (await session.exec(stmt)).all()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    if not messages:
        raise HTTPException(status_code=404, detail="No messages found with the given session ID")

    try:
        # 实现：批量替换会话 ID。
        for message in messages:
            message.session_id = new_session_id

        session.add_all(messages)

        await session.flush()
        message_responses = []
        for message in messages:
            await session.refresh(message)
            message_responses.append(MessageResponse.model_validate(message, from_attributes=True))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    return message_responses


@router.delete("/messages/session/{session_id}", status_code=204, dependencies=[Depends(get_current_active_user)])
async def delete_messages_session(
    session_id: str,
    session: DbSession,
):
    """删除指定会话的全部消息。"""
    try:
        await session.exec(
            delete(MessageTable)
            .where(col(MessageTable.session_id) == session_id)
            .execution_options(synchronize_session="fetch")
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    return {"message": "Messages deleted successfully"}


@router.get("/transactions", dependencies=[Depends(get_current_active_user)])
async def get_transactions(
    flow_id: Annotated[UUID, Query()],
    session: DbSession,
    params: Annotated[Params | None, Depends(custom_params)],
) -> Page[TransactionLogsResponse]:
    """分页获取流程交易日志。"""
    try:
        stmt = (
            select(TransactionTable)
            .where(TransactionTable.flow_id == flow_id)
            .order_by(col(TransactionTable.timestamp).desc())
        )
        import warnings

        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore", category=DeprecationWarning, module=r"fastapi_pagination\.ext\.sqlalchemy"
            )
            return await apaginate(session, stmt, params=params, transformer=transform_transaction_table_for_logs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
