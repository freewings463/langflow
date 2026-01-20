"""
模块名称：`API Key` 数据访问

本模块提供 `API Key` 的创建、删除与校验逻辑。
主要功能包括：生成随机密钥、按配置来源校验密钥与返回关联用户。

关键组件：`get_api_keys` / `create_api_key` / `check_key`
设计背景：集中管理密钥生命周期与校验策略，避免逻辑分散。
使用场景：密钥管理接口、鉴权中间件。
注意事项：`API_KEY_SOURCE=env` 时优先读取环境变量并回退数据库。
"""

import datetime
import os
import secrets
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from langflow.services.database.models.api_key.model import ApiKey, ApiKeyCreate, ApiKeyRead, UnmaskedApiKeyRead
from langflow.services.database.models.user.model import User
from langflow.services.deps import get_settings_service

if TYPE_CHECKING:
    from sqlmodel.sql.expression import SelectOfScalar


async def get_api_keys(session: AsyncSession, user_id: UUID) -> list[ApiKeyRead]:
    """获取指定用户的 `API Key` 列表。

    契约：
    - 输入：`session` 与 `user_id`。
    - 输出：`ApiKeyRead` 列表（已遮罩）。
    - 副作用：读取数据库。
    - 失败语义：查询异常透传。

    关键路径：
    1) 查询用户相关 `ApiKey`。
    2) 使用 `ApiKeyRead` 进行模型验证与遮罩。

    决策：统一返回遮罩模型。
    问题：避免密钥明文泄露。
    方案：在读取层强制转换为 `ApiKeyRead`。
    代价：无法在该接口获取明文密钥。
    重评：当需要返回明文时新增专用接口。
    """
    query: SelectOfScalar = select(ApiKey).where(ApiKey.user_id == user_id)
    api_keys = (await session.exec(query)).all()
    return [ApiKeyRead.model_validate(api_key) for api_key in api_keys]


async def create_api_key(session: AsyncSession, api_key_create: ApiKeyCreate, user_id: UUID) -> UnmaskedApiKeyRead:
    """创建新的 `API Key` 并返回明文。

    契约：
    - 输入：`session`、`api_key_create` 与 `user_id`。
    - 输出：`UnmaskedApiKeyRead`，包含明文 `api_key`。
    - 副作用：写入数据库并生成随机密钥。
    - 失败语义：写入失败异常透传。

    关键路径：
    1) 生成随机密钥字符串。
    2) 创建 `ApiKey` 并持久化。
    3) 返回明文读取模型。

    决策：在服务端生成随机密钥。
    问题：避免客户端生成弱密钥。
    方案：使用 `secrets.token_urlsafe(32)` 生成前缀密钥。
    代价：无法由客户端指定密钥格式。
    重评：当需要自定义密钥时开放可选输入。
    """
    # 注意：使用 32 字节随机数生成密钥前缀。
    generated_api_key = f"sk-{secrets.token_urlsafe(32)}"

    api_key = ApiKey(
        api_key=generated_api_key,
        name=api_key_create.name,
        user_id=user_id,
        created_at=api_key_create.created_at or datetime.datetime.now(datetime.timezone.utc),
    )

    session.add(api_key)
    await session.flush()
    await session.refresh(api_key)
    unmasked = UnmaskedApiKeyRead.model_validate(api_key, from_attributes=True)
    unmasked.api_key = generated_api_key
    return unmasked


async def delete_api_key(session: AsyncSession, api_key_id: UUID, user_id: UUID) -> None:
    """删除指定 `API Key`。

    契约：
    - 输入：`session`、`api_key_id`、`user_id`。
    - 输出：`None`。
    - 副作用：删除数据库记录。
    - 失败语义：未找到或非本人密钥时抛 `ValueError`。

    关键路径：
    1) 读取目标 `ApiKey`。
    2) 校验归属关系。
    3) 删除记录。

    决策：归属不匹配视为未找到。
    问题：避免泄露密钥存在性。
    方案：非本人访问返回相同错误信息。
    代价：调试时难以区分原因。
    重评：当需要审计时改为区分错误类型。
    """
    api_key = await session.get(ApiKey, api_key_id)
    if api_key is None:
        msg = "API Key not found"
        raise ValueError(msg)
    if api_key.user_id != user_id:
        msg = "API Key not found"
        raise ValueError(msg)
    await session.delete(api_key)


async def check_key(session: AsyncSession, api_key: str) -> User | None:
    """校验 `API Key` 并返回对应用户。

    契约：
    - 输入：`session` 与 `api_key`。
    - 输出：匹配到用户则返回 `User`，否则 `None`。
    - 副作用：可能更新密钥使用次数与时间戳。
    - 失败语义：查询异常透传。

    关键路径：
    1) 读取 `API_KEY_SOURCE` 配置。
    2) `env` 模式优先校验环境变量。
    3) 回退到数据库校验。

    决策：环境变量校验失败时回退数据库。
    问题：运维可能使用环境变量覆盖默认密钥。
    方案：优先 `env`，失败则查询数据库。
    代价：配置不一致时可能出现多源规则。
    重评：当单一来源策略稳定后移除回退逻辑。
    """
    settings_service = get_settings_service()
    api_key_source = settings_service.auth_settings.API_KEY_SOURCE

    if api_key_source == "env":
        user = await _check_key_from_env(session, api_key, settings_service)
        if user is not None:
            return user
        # 注意：环境变量校验失败时回退到数据库校验。
    return await _check_key_from_db(session, api_key, settings_service)


async def _check_key_from_db(session: AsyncSession, api_key: str, settings_service) -> User | None:
    """在数据库中校验密钥并返回用户。

    契约：
    - 输入：`session`、`api_key`、`settings_service`。
    - 输出：匹配用户或 `None`。
    - 副作用：可选更新 `total_uses` 与 `last_used_at`。
    - 失败语义：查询异常透传。

    关键路径：
    1) 按 `api_key` 查询并预加载 `user`。
    2) 可选更新使用统计。
    3) 返回关联用户。

    决策：仅在未禁用统计时更新使用计数。
    问题：高频鉴权写入会放大数据库压力。
    方案：支持 `disable_track_apikey_usage` 关闭统计。
    代价：关闭统计会失去使用审计能力。
    重评：当引入异步统计管道时可始终记录。
    """
    query: SelectOfScalar = select(ApiKey).options(selectinload(ApiKey.user)).where(ApiKey.api_key == api_key)
    api_key_object: ApiKey | None = (await session.exec(query)).first()
    if api_key_object is not None:
        if settings_service.settings.disable_track_apikey_usage is not True:
            api_key_object.total_uses += 1
            api_key_object.last_used_at = datetime.datetime.now(datetime.timezone.utc)
            session.add(api_key_object)
            await session.flush()
        return api_key_object.user
    return None


async def _check_key_from_env(session: AsyncSession, api_key: str, settings_service) -> User | None:
    """在环境变量中校验密钥并返回超级用户。

    契约：
    - 输入：`session`、`api_key`、`settings_service`。
    - 输出：匹配时返回 `User`，否则 `None`。
    - 副作用：读取环境变量 `LANGFLOW_API_KEY`。
    - 失败语义：查询异常透传。

    关键路径：
    1) 读取 `LANGFLOW_API_KEY`。
    2) 比对输入密钥。
    3) 返回配置的 `SUPERUSER`。

    决策：环境变量命中时返回超级用户用于授权。
    问题：环境密钥需要统一映射到一个授权主体。
    方案：复用 `SUPERUSER` 作为授权主体。
    代价：权限粒度较粗。
    重评：当支持多密钥映射时改为查表或映射表。
    """
    from langflow.services.database.models.user.crud import get_user_by_username

    env_api_key = os.getenv("LANGFLOW_API_KEY")
    if not env_api_key:
        return None

    # 注意：仅在完全匹配时视为有效。
    if api_key != env_api_key:
        return None

    # 注意：命中环境密钥时返回 `SUPERUSER`。
    superuser_username = settings_service.auth_settings.SUPERUSER
    user = await get_user_by_username(session, superuser_username)
    if user and user.is_active:
        return user
    return None
