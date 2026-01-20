"""
模块名称：认证与令牌工具

本模块提供认证鉴权、令牌生成与 `API key` 处理的核心工具函数，主要用于 `API`、`WebSocket` 与 `Webhook` 鉴权。
主要功能包括：
- `JWT` 验证与签名密钥选择、访问/刷新令牌生成与验证。
- `API key` 校验、自动登录回退与用户解析。
- `Webhook` 与 `MCP` 特殊鉴权路径处理。
- 密码哈希与 `Fernet` 加解密辅助工具。

关键组件：`api_key_security`、`get_current_user_by_jwt`、`create_token`、`decrypt_api_key`。
设计背景：统一认证逻辑并复用安全边界，避免鉴权分散导致不一致。
使用场景：`API` 依赖注入、`WebSocket` 鉴权、`Webhook` 执行、用户令牌签发。
注意事项：错误日志包含关键字如 `JWT`、`API key`，用于排障定位。
"""

import base64
import random
import warnings
from collections.abc import Coroutine
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Annotated, Final
from uuid import UUID

import jwt
from cryptography.fernet import Fernet
from fastapi import Depends, HTTPException, Request, Security, WebSocketException, status
from fastapi.security import APIKeyHeader, APIKeyQuery, OAuth2PasswordBearer
from jwt import InvalidTokenError
from lfx.log.logger import logger
from lfx.services.deps import injectable_session_scope, session_scope
from lfx.services.settings.service import SettingsService
from sqlalchemy.exc import IntegrityError
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.websockets import WebSocket

from langflow.helpers.user import get_user_by_flow_id_or_endpoint_name
from langflow.services.database.models.api_key.crud import check_key
from langflow.services.database.models.user.crud import get_user_by_id, get_user_by_username, update_user_last_login_at
from langflow.services.database.models.user.model import User, UserRead
from langflow.services.deps import get_settings_service

if TYPE_CHECKING:
    from langflow.services.database.models.api_key.model import ApiKey

oauth2_login = OAuth2PasswordBearer(tokenUrl="api/v1/login", auto_error=False)

API_KEY_NAME = "x-api-key"

api_key_query = APIKeyQuery(name=API_KEY_NAME, scheme_name="API key query", auto_error=False)
api_key_header = APIKeyHeader(name=API_KEY_NAME, scheme_name="API key header", auto_error=False)

MINIMUM_KEY_LENGTH = 32
AUTO_LOGIN_WARNING = "In v2.0, LANGFLOW_SKIP_AUTH_AUTO_LOGIN will be removed. Please update your authentication method."
AUTO_LOGIN_ERROR = (
    "Since v1.5, LANGFLOW_AUTO_LOGIN requires a valid API key. "
    "Set LANGFLOW_SKIP_AUTH_AUTO_LOGIN=true to skip this check. "
    "Please update your authentication method."
)

REFRESH_TOKEN_TYPE: Final[str] = "refresh"  # noqa: S105
ACCESS_TOKEN_TYPE: Final[str] = "access"  # noqa: S105

# 注意：`JWT` 密钥配置错误提示文案。
PUBLIC_KEY_NOT_CONFIGURED_ERROR: Final[str] = (
    "Server configuration error: Public key not configured for asymmetric JWT algorithm."
)
SECRET_KEY_NOT_CONFIGURED_ERROR: Final[str] = "Server configuration error: Secret key not configured."  # noqa: S105


class JWTKeyError(HTTPException):
    """`JWT` 密钥配置错误异常。

    契约：用于在鉴权链路中抛出 401；输出为 `HTTPException`。
    关键路径：由 `get_jwt_verification_key` 或 `get_jwt_signing_key` 触发。
    决策：统一使用 401 而非 500
    问题：密钥缺失时需要明确告知客户端重新认证
    方案：抛出带 `WWW-Authenticate` 的 `HTTPException`
    代价：将配置错误暴露为鉴权失败
    重评：若需要区分配置错误与用户错误时
    """

    def __init__(self, detail: str, *, include_www_authenticate: bool = True):
        """构造 `JWT` 密钥异常。

        契约：输入 `detail` 与是否包含 `WWW-Authenticate`，输出为异常实例。
        关键路径：初始化响应头并调用父类构造。
        决策：默认包含 `WWW-Authenticate`
        问题：需提示客户端走 `Bearer` 认证流程
        方案：在 401 响应头写入 `WWW-Authenticate: Bearer`
        代价：额外暴露认证方案信息
        重评：若改为非 Bearer 令牌方案
        """
        headers = {"WWW-Authenticate": "Bearer"} if include_www_authenticate else None
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers=headers,
        )


def get_jwt_verification_key(settings_service: SettingsService) -> str:
    """获取 `JWT` 验证密钥。

    契约：输入 `settings_service`，输出用于验签的字符串密钥；副作用：记录配置错误日志。
    关键路径：根据算法选择公钥或密钥并校验是否配置。
    决策：按算法类型分流密钥来源
    问题：对称/非对称算法需要不同密钥材料
    方案：非对称返回 `PUBLIC_KEY`，对称返回 `SECRET_KEY`
    代价：配置不全时直接抛 401
    重评：若引入多租户或多密钥轮换
    """
    algorithm = settings_service.auth_settings.ALGORITHM

    if algorithm.is_asymmetric():
        verification_key = settings_service.auth_settings.PUBLIC_KEY
        if not verification_key:
            logger.error("Public key is not set in settings for RS256/RS512.")
            raise JWTKeyError(PUBLIC_KEY_NOT_CONFIGURED_ERROR)
        return verification_key

    secret_key = settings_service.auth_settings.SECRET_KEY.get_secret_value()
    if secret_key is None:
        logger.error("Secret key is not set in settings.")
        raise JWTKeyError(SECRET_KEY_NOT_CONFIGURED_ERROR)
    return secret_key


def get_jwt_signing_key(settings_service: SettingsService) -> str:
    """获取 `JWT` 签名密钥。

    契约：输入 `settings_service`，输出用于签名的字符串密钥；副作用：无。
    关键路径：按算法类型返回 `PRIVATE_KEY` 或 `SECRET_KEY`。
    决策：签名密钥与验签密钥分离
    问题：非对称算法需要私钥签名
    方案：非对称返回私钥，对称返回共享密钥
    代价：私钥管理复杂度提高
    重评：当签名改为外部 KMS 时
    """
    algorithm = settings_service.auth_settings.ALGORITHM

    if algorithm.is_asymmetric():
        return settings_service.auth_settings.PRIVATE_KEY.get_secret_value()

    return settings_service.auth_settings.SECRET_KEY.get_secret_value()


# 注意：实现参考 `fastapi_simple_security` 的 `API key` 校验流程。
async def api_key_security(
    query_param: Annotated[str, Security(api_key_query)],
    header_param: Annotated[str, Security(api_key_header)],
) -> UserRead | None:
    """基于 `API key` 的用户鉴权。

    契约：输入 query/header 的 `API key`，输出 `UserRead` 或 `None`；副作用：访问数据库。
    关键路径（三步）：
    1) 处理 `AUTO_LOGIN` 与 `skip_auth_auto_login` 回退逻辑。
    2) 校验 `API key` 并转换为 `UserRead`。
    3) 不合法时抛 403 终止请求。
    异常流：缺少 `superuser` 或 `API key` 无效时抛 `HTTPException`。
    排障入口：日志关键字 `AUTO_LOGIN_WARNING`、`Invalid or missing API key`。
    决策：同时支持 `header`/`query` 两种传参
    问题：历史客户端存在多种传参方式
    方案：优先使用传入值并统一走 `check_key`
    代价：校验入口变多，误配置更难发现
    重评：当 `API key` 传参规范收敛为单一渠道
    """
    settings_service = get_settings_service()
    result: ApiKey | User | None

    async with session_scope() as db:
        if settings_service.auth_settings.AUTO_LOGIN:
            # 注意：自动登录场景优先读取配置的超级用户。
            if not settings_service.auth_settings.SUPERUSER:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Missing first superuser credentials",
                )
            if not query_param and not header_param:
                if settings_service.auth_settings.skip_auth_auto_login:
                    result = await get_user_by_username(db, settings_service.auth_settings.SUPERUSER)
                    logger.warning(AUTO_LOGIN_WARNING)
                    return UserRead.model_validate(result, from_attributes=True)
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=AUTO_LOGIN_ERROR,
                )
            result = await check_key(db, query_param or header_param)

        elif not query_param and not header_param:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="An API key must be passed as query or header",
            )

        else:
            result = await check_key(db, query_param or header_param)

        if not result:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid or missing API key",
            )

        if isinstance(result, User):
            return UserRead.model_validate(result, from_attributes=True)

    msg = "Invalid result type"
    raise ValueError(msg)


async def ws_api_key_security(
    api_key: str | None,
) -> UserRead:
    """`WebSocket` `API key` 鉴权。

    契约：输入 `api_key`，输出 `UserRead`；副作用：访问数据库。
    关键路径（三步）：
    1) 处理 `AUTO_LOGIN` 与 `superuser` 回退。
    2) 校验 `API key` 并转换为 `UserRead`。
    3) 失败时抛 `WebSocketException` 并终止连接。
    异常流：缺少 `API key` 或无效时返回 `WS_1008`。
    排障入口：日志关键字 `AUTO_LOGIN_WARNING`、`Invalid or missing API key`。
    决策：`WebSocket` 失败用 `WS` 码而非 `HTTP` 码
    问题：`WS` 握手阶段无法返回标准 `HTTP` 错误体
    方案：使用 `WebSocketException` 标准码
    代价：客户端需处理 `WS` 关闭码
    重评：若切换到统一的 `WS` 鉴权中间件
    """
    settings = get_settings_service()
    async with session_scope() as db:
        if settings.auth_settings.AUTO_LOGIN:
            if not settings.auth_settings.SUPERUSER:
                # 注意：服务端配置缺失导致的内部错误。
                raise WebSocketException(
                    code=status.WS_1011_INTERNAL_ERROR,
                    reason="Missing first superuser credentials",
                )
            if not api_key:
                if settings.auth_settings.skip_auth_auto_login:
                    result = await get_user_by_username(db, settings.auth_settings.SUPERUSER)
                    logger.warning(AUTO_LOGIN_WARNING)
                else:
                    raise WebSocketException(
                        code=status.WS_1008_POLICY_VIOLATION,
                        reason=AUTO_LOGIN_ERROR,
                    )
            else:
                result = await check_key(db, api_key)

        # 注意：常规路径必须提供 `API key`。
        else:
            if not api_key:
                raise WebSocketException(
                    code=status.WS_1008_POLICY_VIOLATION,
                    reason="An API key must be passed as query or header",
                )
            result = await check_key(db, api_key)

        # 注意：`API key` 无效或缺失。
        if not result:
            raise WebSocketException(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="Invalid or missing API key",
            )

        # 注意：将 `SQLModel` `User` 转换为 `UserRead`。
        if isinstance(result, User):
            return UserRead.model_validate(result, from_attributes=True)

    # 注意：兜底处理未预期的鉴权结果。
    raise WebSocketException(
        code=status.WS_1011_INTERNAL_ERROR,
        reason="Authentication subsystem error",
    )


async def get_current_user(
    token: Annotated[str, Security(oauth2_login)],
    query_param: Annotated[str, Security(api_key_query)],
    header_param: Annotated[str, Security(api_key_header)],
    db: Annotated[AsyncSession, Depends(injectable_session_scope)],
) -> User:
    """获取当前用户（`JWT` 优先）。

    契约：输入 `JWT` 或 `API key`，输出 `User`；副作用：访问数据库。
    关键路径：优先 `JWT`，其次 `API key`；均失败则抛 403。
    决策：`JWT` 优先于 `API key`
    问题：同时提供多种凭据时需统一优先级
    方案：先校验 `token`，失败再走 `API key`
    代价：无法同时验证多凭据一致性
    重评：若引入多因子或复合鉴权
    """
    if token:
        return await get_current_user_by_jwt(token, db)
    user = await api_key_security(query_param, header_param)
    if user:
        return user

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Invalid or missing API key",
    )


async def get_current_user_by_jwt(
    token: str,
    db: AsyncSession,
) -> User:
    """使用 `JWT` 获取当前用户。

    契约：输入 `token` 与数据库会话，输出 `User`；副作用：访问数据库与日志记录。
    关键路径（三步）：
    1) 解析算法并获取验签密钥。
    2) 解码 `JWT` 并校验 `type/exp/sub`。
    3) 查询用户并检查 `is_active`。
    异常流：验签失败、过期或用户无效时抛 401。
    排障入口：日志关键字 `JWT validation failed`、`Token expired`。
    决策：强制校验 `type` 为 `access`
    问题：刷新令牌误用会绕过权限控制
    方案：校验 `type == access` 并拒绝其他类型
    代价：客户端需区分 access/refresh 的使用场景
    重评：若引入统一令牌或精细化权限类型
    """
    settings_service = get_settings_service()

    if isinstance(token, Coroutine):
        token = await token

    algorithm = settings_service.auth_settings.ALGORITHM
    verification_key = get_jwt_verification_key(settings_service)

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            payload = jwt.decode(token, verification_key, algorithms=[algorithm])
        user_id: UUID = payload.get("sub")  # type: ignore[assignment]
        token_type: str = payload.get("type")  # type: ignore[assignment]

        if token_type != ACCESS_TOKEN_TYPE:
            logger.error(f"Token type is invalid: {token_type}. Expected: {ACCESS_TOKEN_TYPE}.")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token is invalid.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if expires := payload.get("exp", None):
            expires_datetime = datetime.fromtimestamp(expires, timezone.utc)
            if datetime.now(timezone.utc) > expires_datetime:
                logger.info("Token expired for user")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token has expired.",
                    headers={"WWW-Authenticate": "Bearer"},
                )

        if user_id is None or token_type is None:
            logger.info(f"Invalid token payload. Token type: {token_type}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token details.",
                headers={"WWW-Authenticate": "Bearer"},
            )
    except InvalidTokenError as e:
        logger.debug("JWT validation failed: Invalid token format or signature")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e

    user = await get_user_by_id(db, user_id)
    if user is None or not user.is_active:
        logger.info("User not found or inactive.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or is inactive.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def get_current_user_for_websocket(
    websocket: WebSocket,
    db: AsyncSession,
) -> User | UserRead:
    """`WebSocket` 获取当前用户。

    契约：从 cookie/query/header 解析凭据并返回用户；副作用：访问数据库。
    关键路径（三步）：
    1) 优先读取 `access_token_lf` 或 `token`。
    2) 若无 `JWT`，尝试 `API key` 多位置解析。
    3) 均失败则抛 `WS_1008` 终止连接。
    异常流：鉴权失败返回 `WebSocketException`。
    排障入口：错误原因 `Missing or invalid credentials`。
    决策：支持多位置读取 `API key`
    问题：不同客户端在 WS 中传参方式不一致
    方案：兼容 query/header 多字段
    代价：安全面扩大，需要更严格审计
    重评：当 WS 客户端规范统一后
    """
    token = websocket.cookies.get("access_token_lf") or websocket.query_params.get("token")
    if token:
        user = await get_current_user_by_jwt(token, db)
        if user:
            return user

    api_key = (
        websocket.query_params.get("x-api-key")
        or websocket.query_params.get("api_key")
        or websocket.headers.get("x-api-key")
        or websocket.headers.get("api_key")
    )
    if api_key:
        user_read = await ws_api_key_security(api_key)
        if user_read:
            return user_read

    raise WebSocketException(
        code=status.WS_1008_POLICY_VIOLATION, reason="Missing or invalid credentials (cookie, token or API key)."
    )


async def get_current_active_user(current_user: Annotated[User, Depends(get_current_user)]):
    """校验当前用户是否激活。

    契约：输入 `current_user`，输出激活用户；副作用：无。
    关键路径：检测 `is_active`，不满足则抛 401。
    决策：未激活统一返回 401
    问题：需要阻止未激活用户访问受限资源
    方案：在依赖层抛出 `HTTPException`
    代价：无法区分未激活与权限不足
    重评：若需要更细粒度的状态码区分
    """
    if not current_user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inactive user")
    return current_user


async def get_current_active_superuser(current_user: Annotated[User, Depends(get_current_user)]) -> User:
    """校验当前用户是否为激活超级用户。

    契约：输入 `current_user`，输出超级用户；副作用：无。
    关键路径：先校验激活，再校验 `is_superuser`。
    决策：按顺序先验激活再验权限
    问题：避免对未激活账户泄露权限信息
    方案：先 401 再 403
    代价：调试时需区分两类失败
    重评：若权限系统引入统一的授权层
    """
    if not current_user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inactive user")
    if not current_user.is_superuser:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="The user doesn't have enough privileges")
    return current_user


async def get_webhook_user(flow_id: str, request: Request) -> UserRead:
    """获取 `webhook` 执行用户。

    契约：输入 `flow_id` 与请求对象，输出 `UserRead`；副作用：访问数据库与日志。
    关键路径（三步）：
    1) 根据 `WEBHOOK_AUTH_ENABLE` 决定是否跳过 `API key`。
    2) 校验 `API key` 并获取认证用户。
    3) 校验 flow 所有权并返回用户。
    异常流：`API key` 缺失/无效或所有权不匹配时抛 403。
    排障入口：日志关键字 `Webhook API key`、`Flow not found`。
    决策：关闭 `webhook` 鉴权时允许按流程所有者执行
    问题：需要兼容历史无鉴权 webhook 调用
    方案：基于配置开关放行并校验 flow 归属
    代价：关闭鉴权时存在更高的执行风险
    重评：当全部用户迁移到强制鉴权模式
    """
    settings_service = get_settings_service()

    if not settings_service.auth_settings.WEBHOOK_AUTH_ENABLE:
    # 注意：关闭 `webhook` 鉴权时允许以流程所有者执行且不要求 `API key`。
        try:
            flow_owner = await get_user_by_flow_id_or_endpoint_name(flow_id)
            if flow_owner is None:
                raise HTTPException(status_code=404, detail="Flow not found")
            return flow_owner  # noqa: TRY300
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=404, detail="Flow not found") from exc

    # 注意：开启 `webhook` 鉴权时必须校验 `API key`。
    api_key_header_val = request.headers.get("x-api-key")
    api_key_query_val = request.query_params.get("x-api-key")

    # 注意：检查是否提供 `API key`。
    if not api_key_header_val and not api_key_query_val:
        raise HTTPException(status_code=403, detail="API key required when webhook authentication is enabled")

    # 注意：优先使用 `header` 的 `API key`，其次 `query`。
    api_key = api_key_header_val or api_key_query_val

    try:
        # 注意：`webhook` 场景不走 `AUTO_LOGIN` 回退。
        async with session_scope() as db:
            result = await check_key(db, api_key)
            if not result:
                logger.warning("Invalid API key provided for webhook")
                raise HTTPException(status_code=403, detail="Invalid API key")

            authenticated_user = UserRead.model_validate(result, from_attributes=True)
            logger.info("Webhook API key validated successfully")
    except HTTPException:
        # 注意：`HTTPException` 直接向上抛出。
        raise
    except Exception as exc:
        # 注意：其他异常统一转换为鉴权失败。
        logger.error(f"Webhook API key validation error: {exc}")
        raise HTTPException(status_code=403, detail="API key authentication failed") from exc

    # 注意：验证认证用户是否为流程所有者。
    try:
        flow_owner = await get_user_by_flow_id_or_endpoint_name(flow_id)
        if flow_owner is None:
            raise HTTPException(status_code=404, detail="Flow not found")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=404, detail="Flow not found") from exc

    if flow_owner.id != authenticated_user.id:
        raise HTTPException(status_code=403, detail="Access denied: You can only execute webhooks for flows you own")

    return authenticated_user


def verify_password(plain_password, hashed_password):
    """验证明文密码与哈希是否匹配。

    契约：输入明文与哈希，输出布尔；副作用：无。
    关键路径：调用 `pwd_context.verify`。
    决策：使用 `pwd_context` 统一算法
    问题：多处密码校验需一致算法与参数
    方案：依赖配置中的 `pwd_context`
    代价：算法升级需同步配置
    重评：若迁移到外部身份提供方
    """
    settings_service = get_settings_service()
    return settings_service.auth_settings.pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password):
    """生成密码哈希。

    契约：输入明文密码，输出哈希字符串；副作用：无。
    关键路径：调用 `pwd_context.hash`。
    决策：使用同一 `pwd_context` 生成哈希
    问题：需要与验证逻辑保持一致
    方案：复用配置中的密码上下文
    代价：配置错误会影响全局登录
    重评：若引入多租户不同哈希策略
    """
    settings_service = get_settings_service()
    return settings_service.auth_settings.pwd_context.hash(password)


def create_token(data: dict, expires_delta: timedelta):
    """创建 `JWT` 访问/刷新令牌。

    契约：输入载荷与过期时间，输出编码后的 `JWT`；副作用：无。
    关键路径：补充 `exp` 并按算法签名。
    决策：统一在此处写入 `exp`
    问题：不同调用方容易遗漏过期时间
    方案：集中处理并共享签名逻辑
    代价：必须传入 `expires_delta`
    重评：若引入可配置的默认过期策略
    """
    settings_service = get_settings_service()

    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + expires_delta
    to_encode["exp"] = expire

    algorithm = settings_service.auth_settings.ALGORITHM
    signing_key = get_jwt_signing_key(settings_service)

    return jwt.encode(
        to_encode,
        signing_key,
        algorithm=algorithm,
    )


async def create_super_user(
    username: str,
    password: str,
    db: AsyncSession,
) -> User:
    """创建或获取超级用户。

    契约：输入用户名/密码与会话，输出 `User`；副作用：写入数据库。
    关键路径（三步）：
    1) 查询已有超级用户。
    2) 不存在则创建并提交事务。
    3) 处理并发冲突后返回实例。
    异常流：提交失败或并发冲突未能恢复时抛异常。
    排障入口：日志关键字 `Error creating superuser`。
    决策：幂等创建超级用户
    问题：多实例启动可能重复创建
    方案：先查后插，并在冲突时回滚重查
    代价：需要额外一次查询
    重评：若引入集中式用户初始化流程
    """
    super_user = await get_user_by_username(db, username)

    if not super_user:
        super_user = User(
            username=username,
            password=get_password_hash(password),
            is_superuser=True,
            is_active=True,
            last_login_at=None,
        )

        db.add(super_user)
        try:
            await db.commit()
            await db.refresh(super_user)
        except IntegrityError:
            # 注意：并发创建导致唯一约束冲突，回滚后重查。
            await db.rollback()
            super_user = await get_user_by_username(db, username)
            if not super_user:
                raise  # 注意：非并发冲突时直接向上抛出。
        except Exception:  # noqa: BLE001
            logger.debug("Error creating superuser.", exc_info=True)

    return super_user


async def create_user_longterm_token(db: AsyncSession) -> tuple[UUID, dict]:
    """创建长期访问令牌（自动登录模式）。

    契约：输入数据库会话，输出 `(user_id, token_dict)`；副作用：更新 `last_login_at`。
    关键路径（三步）：
    1) 校验 `AUTO_LOGIN` 并选择超级用户。
    2) 生成 365 天 access token。
    3) 更新 `last_login_at` 并返回结果。
    异常流：缺少超级用户或未启用自动登录时抛 400。
    排障入口：错误信息 `Auto login required`、`Super user hasn't been created`。
    决策：长期令牌只在自动登录模式提供
    问题：避免在常规鉴权流程中生成超长令牌
    方案：强制检查 `AUTO_LOGIN`
    代价：非自动登录场景无法使用该功能
    重评：若引入显式的长期令牌管理接口
    """
    settings_service = get_settings_service()
    if not settings_service.auth_settings.AUTO_LOGIN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Auto login required to create a long-term token"
        )

    # 注意：优先使用配置的超级用户名；仅在自动登录时生效。
    username = settings_service.auth_settings.SUPERUSER
    super_user = await get_user_by_username(db, username)
    if not super_user:
        from langflow.services.database.models.user.crud import get_all_superusers

        superusers = await get_all_superusers(db)
        super_user = superusers[0] if superusers else None

    if not super_user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Super user hasn't been created")
    access_token_expires_longterm = timedelta(days=365)
    access_token = create_token(
        data={"sub": str(super_user.id), "type": ACCESS_TOKEN_TYPE},
        expires_delta=access_token_expires_longterm,
    )

    # 注意：写入 `last_login_at` 作为令牌签发痕迹。
    await update_user_last_login_at(super_user.id, db)

    return super_user.id, {
        "access_token": access_token,
        "refresh_token": None,
        "token_type": "bearer",
    }


def create_user_api_key(user_id: UUID) -> dict:
    """创建用户 `API key` 令牌。

    契约：输入 `user_id`，输出包含 `api_key` 的字典；副作用：无。
    关键路径：生成 2 年有效期的 `JWT`。
    决策：`API key` 复用 `JWT` 结构
    问题：需要可验证且可过期的 `API key`
    方案：以 `JWT` 形式签发并设置长过期
    代价：令牌体积较大且暴露 `sub`
    重评：若改为随机前缀+数据库存储方案
    """
    access_token = create_token(
        data={"sub": str(user_id), "type": "api_key"},
        expires_delta=timedelta(days=365 * 2),
    )

    return {"api_key": access_token}


def get_user_id_from_token(token: str) -> UUID:
    """从 `JWT` 中解析用户 ID（不验签）。

    契约：输入 token，输出 `UUID`；副作用：无。
    关键路径：关闭签名验证读取 `sub`。
    决策：仅用于解析而非鉴权
    问题：某些流程需要快速拿到 `sub`
    方案：使用 `verify_signature=False` 解码
    代价：存在被篡改的风险
    重评：若需要安全解析则改为验签
    """
    try:
        claims = jwt.decode(token, options={"verify_signature": False})
        user_id = claims["sub"]
        return UUID(user_id)
    except (KeyError, InvalidTokenError, ValueError):
        return UUID(int=0)


async def create_user_tokens(user_id: UUID, db: AsyncSession, *, update_last_login: bool = False) -> dict:
    """创建访问/刷新令牌对。

    契约：输入 `user_id`、数据库会话与更新标志，输出令牌字典；副作用：可更新 `last_login_at`。
    关键路径：生成 access 与 refresh 两类 `JWT`。
    决策：同时签发 access 与 refresh
    问题：需要在短期访问与续期之间平衡
    方案：分离两类 token 并配置不同过期
    代价：客户端需管理刷新流程
    重评：若改为无状态单令牌方案
    """
    settings_service = get_settings_service()

    access_token_expires = timedelta(seconds=settings_service.auth_settings.ACCESS_TOKEN_EXPIRE_SECONDS)
    access_token = create_token(
        data={"sub": str(user_id), "type": ACCESS_TOKEN_TYPE},
        expires_delta=access_token_expires,
    )

    refresh_token_expires = timedelta(seconds=settings_service.auth_settings.REFRESH_TOKEN_EXPIRE_SECONDS)
    refresh_token = create_token(
        data={"sub": str(user_id), "type": REFRESH_TOKEN_TYPE},
        expires_delta=refresh_token_expires,
    )

    # 注意：可选更新 `last_login_at`，用于审计与活跃度统计。
    if update_last_login:
        await update_user_last_login_at(user_id, db)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


async def create_refresh_token(refresh_token: str, db: AsyncSession):
    """用 `refresh token` 换发新的令牌对。

    契约：输入 `refresh token` 与数据库会话，输出新的令牌字典；副作用：访问数据库。
    关键路径（三步）：
    1) 解析并校验 `refresh token` 类型与用户。
    2) 校验用户存在且处于激活状态。
    3) 生成并返回新的 token 对。
    异常流：token 无效或用户不可用时抛 401。
    排障入口：日志关键字 `JWT decoding error`。
    决策：`refresh token` 也走 `JWT` 校验
    问题：需要防止伪造 refresh token
    方案：使用同一算法和验签密钥校验
    代价：密钥配置错误会导致全部刷新失败
    重评：若引入 refresh token 黑名单机制
    """
    settings_service = get_settings_service()

    algorithm = settings_service.auth_settings.ALGORITHM
    verification_key = get_jwt_verification_key(settings_service)

    try:
        # 注意：忽略 `datetime.utcnow` 的弃用警告，仅影响解码内部处理。
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            payload = jwt.decode(
                refresh_token,
                verification_key,
                algorithms=[algorithm],
            )
        user_id: UUID = payload.get("sub")  # type: ignore[assignment]
        token_type: str = payload.get("type")  # type: ignore[assignment]

        if user_id is None or token_type != REFRESH_TOKEN_TYPE:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

        user_exists = await get_user_by_id(db, user_id)

        if user_exists is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

        # 安全：确保用户仍处于激活状态。
        if not user_exists.is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User account is inactive")

        return await create_user_tokens(user_id, db)

    except InvalidTokenError as e:
        logger.exception("JWT decoding error")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        ) from e


async def authenticate_user(username: str, password: str, db: AsyncSession) -> User | None:
    """校验用户名与密码并返回用户。

    契约：输入用户名/密码与数据库会话，输出 `User` 或 `None`；副作用：访问数据库。
    关键路径：查询用户、校验激活状态、验证密码。
    决策：未激活区分审批中与失效
    问题：首次登录需等待审批，已禁用需直接拒绝
    方案：根据 `last_login_at` 区分 400/401
    代价：客户端需处理两类错误码
    重评：若审批流程改为独立状态字段
    """
    user = await get_user_by_username(db, username)

    if not user:
        return None

    if not user.is_active:
        if not user.last_login_at:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Waiting for approval")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inactive user")

    return user if verify_password(password, user.password) else None


def add_padding(s):
    """补齐 `Base64` 字符串的 `=` 填充。

    契约：输入字符串，输出补齐后的字符串；副作用：无。
    关键路径：按 4 字节对齐计算补齐长度。
    决策：仅在长度非 4 倍数时补齐
    问题：`Fernet` 密钥需要合法的 `Base64` 长度
    方案：按 `4 - len(s) % 4` 追加 `=`
    代价：对原始字符串进行变更
    重评：若密钥改为固定长度输入
    """
    # 注意：按 `Base64` 规范计算补齐字符数量。
    padding_needed = 4 - len(s) % 4
    return s + "=" * padding_needed


def ensure_valid_key(s: str) -> bytes:
    """确保密钥可用于 `Fernet`。

    契约：输入字符串密钥，输出符合 `Fernet` 要求的字节串；副作用：可能使用随机种子。
    关键路径：短密钥转为伪随机 32 字节，否则进行 Base64 补齐。
    决策：短密钥走派生路径而非直接拒绝
    问题：历史配置可能短于最小长度
    方案：以输入作为随机种子生成 32 字节
    代价：安全性依赖输入质量
    重评：若要求强制最小长度并拒绝短密钥
    """
    # 注意：短密钥会被视作种子生成 32 字节随机值。
    if len(s) < MINIMUM_KEY_LENGTH:
        # 注意：使用输入作为随机种子以保持可复现性。
        random.seed(s)
        # 注意：生成 32 字节以满足 `Fernet` 要求。
        key = bytes(random.getrandbits(8) for _ in range(32))
        key = base64.urlsafe_b64encode(key)
    else:
        key = add_padding(s).encode()
    return key


def get_fernet(settings_service: SettingsService):
    """构造 `Fernet` 实例。

    契约：输入 `settings_service`，输出 `Fernet`；副作用：无。
    关键路径：读取 `SECRET_KEY` 并修正长度。
    决策：统一由此函数创建 `Fernet`
    问题：避免多处重复处理密钥长度
    方案：集中调用 `ensure_valid_key`
    代价：密钥修正逻辑集中带来变更影响面
    重评：若迁移至 KMS 或外部密钥服务
    """
    secret_key: str = settings_service.auth_settings.SECRET_KEY.get_secret_value()
    valid_key = ensure_valid_key(secret_key)
    return Fernet(valid_key)


def encrypt_api_key(api_key: str, settings_service: SettingsService):
    """加密 `API key`。

    契约：输入明文 `api_key` 与 `settings_service`，输出密文字符串；副作用：无。
    关键路径：创建 `Fernet` 并执行加密。
    决策：使用对称加密便于解密回显
    问题：需要在存储时保护明文
    方案：使用 `Fernet` 双向加密
    代价：密钥泄露将导致全部密文失效
    重评：若改为不可逆哈希存储
    """
    fernet = get_fernet(settings_service)
    # 注意：双向加密便于在运行时解密使用。
    encrypted_key = fernet.encrypt(api_key.encode())
    return encrypted_key.decode()


def decrypt_api_key(encrypted_api_key: str, settings_service: SettingsService):
    """解密 `API key`（容错处理）。

    契约：输入密文与 `settings_service`，输出明文或空字符串；副作用：记录 debug 日志。
    关键路径：先对密文编码解密，失败则用原始字符串重试。
    异常流：两次解密均失败时抛异常或返回空字符串。
    决策：提供二次解密回退
    问题：部分密文在存储时未按 UTF-8 编码
    方案：先 `encode()` 解密，失败后用原字符串
    代价：异常路径更复杂，排障需看 debug 日志
    重评：若统一密文存储编码格式
    """
    fernet = get_fernet(settings_service)
    if isinstance(encrypted_api_key, str):
        try:
            return fernet.decrypt(encrypted_api_key.encode()).decode()
        except Exception as primary_exception:  # noqa: BLE001
            logger.debug(
                "Decryption using UTF-8 encoded API key failed. Error: %s. "
                "Retrying decryption using the raw string input.",
                primary_exception,
            )
            return fernet.decrypt(encrypted_api_key).decode()
    return ""


# 注意：`MCP` 鉴权路径始终视为 `skip_auth_auto_login=True`。
async def get_current_user_mcp(
    token: Annotated[str, Security(oauth2_login)],
    query_param: Annotated[str, Security(api_key_query)],
    header_param: Annotated[str, Security(api_key_header)],
    db: Annotated[AsyncSession, Depends(injectable_session_scope)],
) -> User:
    """`MCP` 端点的用户鉴权。

    契约：输入 `JWT` 或 `API key`，输出 `User`；副作用：访问数据库与日志。
    关键路径（三步）：
    1) `JWT` 存在时走标准 `JWT` 校验。
    2) `AUTO_LOGIN` 时允许无 `API key` 回退到 `superuser`。
    3) 其余情况校验 `API key` 并返回用户。
    异常流：缺少凭据或校验失败时抛 403。
    排障入口：日志关键字 `AUTO_LOGIN_WARNING`。
    决策：`MCP` 始终允许无 `API key` 回退
    问题：`MCP` 集成阶段需要兼容旧客户端
    方案：视为 `skip_auth_auto_login=True`
    代价：降低默认安全强度
    重评：`MCP` 全量接入后移除此兼容逻辑
    """
    if token:
        return await get_current_user_by_jwt(token, db)

    # 注意：`MCP` 路径视为开启自动登录回退逻辑。
    settings_service = get_settings_service()
    result: ApiKey | User | None

    if settings_service.auth_settings.AUTO_LOGIN:
        # 注意：使用配置的超级用户作为回退身份。
        if not settings_service.auth_settings.SUPERUSER:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing first superuser credentials",
            )
        if not query_param and not header_param:
            # 注意：`MCP` 无 `API key` 时直接按 `superuser` 回退。
            result = await get_user_by_username(db, settings_service.auth_settings.SUPERUSER)
            if result:
                logger.warning(AUTO_LOGIN_WARNING)
                return result
        else:
            result = await check_key(db, query_param or header_param)

    elif not query_param and not header_param:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="An API key must be passed as query or header",
        )

    elif query_param:
        result = await check_key(db, query_param)

    else:
        result = await check_key(db, header_param)

    if not result:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing API key",
        )

    # 注意：若结果为 `User`，可直接返回。
    if isinstance(result, User):
        return result

    # 注意：若为 `ApiKey`，说明流程异常，返回错误以保持语义明确。
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Invalid authentication result",
    )


async def get_current_active_user_mcp(current_user: Annotated[User, Depends(get_current_user_mcp)]):
    """`MCP` 专用的激活用户依赖。

    契约：输入 `current_user`，输出激活用户；副作用：无。
    关键路径：检查 `is_active` 并在失败时抛 401。
    决策：独立依赖以隔离 `MCP` 兼容逻辑
    问题：`MCP` 接入期需要不同的鉴权路径
    方案：提供单独依赖并在集成完成后移除
    代价：维护两套鉴权入口
    重评：当 `MCP` 完全接入主鉴权链路后
    """
    if not current_user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inactive user")
    return current_user
