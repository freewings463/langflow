"""
模块名称：API Key 管理接口

本模块提供 API Key 的创建、列表、删除与前端存储入口，服务于控制台密钥管理与会话持久化。
主要功能：
- 获取当前用户的 API Key 列表
- 创建与删除 API Key 记录
- 将 API Key 加密写入用户资料并下发会话 Cookie
设计背景：需要在不暴露明文的前提下支持前端鉴权与后端校验。
注意事项：异常统一转为 400；仅保存加密值，明文不落库。
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Response

from langflow.api.utils import CurrentActiveUser, DbSession
from langflow.api.v1.schemas import ApiKeyCreateRequest, ApiKeysResponse
from langflow.services.auth import utils as auth_utils
from langflow.services.database.models.api_key.crud import create_api_key, delete_api_key, get_api_keys
from langflow.services.database.models.api_key.model import ApiKeyCreate, UnmaskedApiKeyRead
from langflow.services.deps import get_settings_service

router = APIRouter(tags=["APIKey"], prefix="/api_key")


@router.get("/")
async def get_api_keys_route(
    db: DbSession,
    current_user: CurrentActiveUser,
) -> ApiKeysResponse:
    """获取当前用户 API Key 列表。

    契约：
    - 输入：`db`、`current_user`
    - 输出：`ApiKeysResponse`（含 `total_count`/`user_id`/`api_keys`）
    - 失败语义：异常转 `HTTPException(400)`，前端需提示错误详情
    """
    try:
        user_id = current_user.id
        keys = await get_api_keys(db, user_id)

        return ApiKeysResponse(total_count=len(keys), user_id=user_id, api_keys=keys)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/")
async def create_api_key_route(
    req: ApiKeyCreate,
    current_user: CurrentActiveUser,
    db: DbSession,
) -> UnmaskedApiKeyRead:
    """创建并返回未脱敏的 API Key。

    契约：
    - 输入：`req`（创建参数）、`current_user`、`db`
    - 输出：`UnmaskedApiKeyRead`
    - 失败语义：异常转 `HTTPException(400)`，调用方需提示并可重试
    """
    try:
        user_id = current_user.id
        return await create_api_key(db, req, user_id=user_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete("/{api_key_id}")
async def delete_api_key_route(
    api_key_id: UUID,
    db: DbSession,
    current_user: CurrentActiveUser,
):
    """删除指定 API Key。

    契约：
    - 输入：`api_key_id`、`current_user`、`db`
    - 输出：`{"detail": "API Key deleted"}`
    - 失败语义：异常转 `HTTPException(400)`，调用方需提示错误
    """
    try:
        await delete_api_key(db, api_key_id, current_user.id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"detail": "API Key deleted"}


@router.post("/store")
async def save_store_api_key(
    api_key_request: ApiKeyCreateRequest,
    response: Response,
    current_user: CurrentActiveUser,
    db: DbSession,
):
    """加密保存 API Key 并写入会话 Cookie。

    契约：
    - 输入：`api_key_request`、`current_user`、`db`、`response`
    - 副作用：更新用户记录并设置 `apikey_tkn_lflw` Cookie
    - 失败语义：异常转 `HTTPException(400)`，前端需提示错误
    """
    settings_service = get_settings_service()
    auth_settings = settings_service.auth_settings

    try:
        api_key = api_key_request.api_key

        # 安全：仅保存加密后的密钥，明文不写入数据库。
        encrypted = auth_utils.encrypt_api_key(api_key, settings_service=settings_service)
        current_user.store_api_key = encrypted
        db.add(current_user)
        await db.commit()

        response.set_cookie(
            "apikey_tkn_lflw",
            encrypted,
            httponly=auth_settings.ACCESS_HTTPONLY,
            samesite=auth_settings.ACCESS_SAME_SITE,
            secure=auth_settings.ACCESS_SECURE,
            expires=None,  # 注意：`expires=None` 表示会话级 Cookie。
            domain=auth_settings.COOKIE_DOMAIN,
        )

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return {"detail": "API Key saved"}
