"""
模块名称：健康检查路由

本模块提供基础与综合健康检查接口，主要用于部署探针与运维监控。主要功能包括：
- 提供 `/health` 兼容旧探针
- 提供 `/health_check` 检测数据库与缓存服务

关键组件：
- health_check_router：路由聚合器
- HealthResponse：健康检查返回模型

设计背景：运维需要快速区分“进程存活”和“核心依赖可用”。
注意事项：返回信息避免泄露敏感异常细节。
"""

import uuid

from fastapi import APIRouter, HTTPException, status
from lfx.log.logger import logger
from pydantic import BaseModel
from sqlmodel import select

from langflow.api.utils import DbSession
from langflow.services.database.models.flow.model import Flow
from langflow.services.deps import get_chat_service
health_check_router = APIRouter(tags=["Health Check"])


class HealthResponse(BaseModel):
    """健康检查返回结构。

    契约：字段为字符串状态值，默认 `nok/error`，健康时置为 `ok`。
    安全：不向客户端返回异常细节，避免凭据泄露。
    """

    status: str = "nok"
    chat: str = "error check the server logs"
    db: str = "error check the server logs"

    def has_error(self) -> bool:
        """判断是否存在错误状态。"""
        return any(v.startswith("error") for v in self.model_dump().values())


# 注意：`/health` 会被 `uvicorn` 先行响应，不能作为 `Langflow` 实例可用性判定，仅用于兼容旧探针。
@health_check_router.get("/health")
async def health():
    """兼容旧探针的存活检查。"""
    return {"status": "ok"}


@health_check_router.get("/health_check")
async def health_check(
    session: DbSession,
) -> HealthResponse:
    """综合健康检查。

    契约：返回 `HealthResponse`；任一依赖失败则抛 `HTTPException(500)`。
    副作用：查询数据库与读写缓存。
    关键路径（三步）：
    1) 执行数据库探测查询。
    2) 执行缓存读写探测。
    3) 汇总状态并返回或抛错。
    排障入口：日志关键字 `Error checking database` / `Error checking chat service`。
    """
    response = HealthResponse()
    user_id = "da93c2bd-c857-4b10-8c8c-60988103320f"
    try:
        stmt = select(Flow).where(Flow.id == uuid.uuid4())
        (await session.exec(stmt)).first()
        response.db = "ok"
    except Exception:  # noqa: BLE001
        await logger.aexception("Error checking database")

    try:
        chat = get_chat_service()
        await chat.set_cache("health_check", str(user_id))
        await chat.get_cache("health_check")
        response.chat = "ok"
    except Exception:  # noqa: BLE001
        await logger.aexception("Error checking chat service")

    if response.has_error():
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=response.model_dump())
    response.status = "ok"
    return response
