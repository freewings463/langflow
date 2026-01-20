"""
模块名称：注册与遥测 API

本模块提供单用户注册信息的写入与查询，并异步发送邮箱遥测事件。
主要功能包括：
- 写入/读取本地注册文件（单条覆盖）
- 发送邮箱注册遥测（best-effort）
- 提供受保护的查询接口

关键组件：
- `save_registration` / `load_registration`
- `register_user` / `get_registration`
- `_send_email_telemetry`

设计背景：部署形态中无需账号系统，仅保留单一注册邮箱用于统计。
注意事项：注册信息存储在本地文件，覆盖写入，非多用户设计。
"""

import json
from asyncio import to_thread
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr

from langflow.logging import logger
from langflow.services.auth.utils import get_current_active_user
from langflow.services.deps import get_telemetry_service
from langflow.services.telemetry.schema import EmailPayload

router = APIRouter(tags=["Registration API"], prefix="/registration")


# 注意：仅用于单用户注册场景，字段保持最小化
class RegisterRequest(BaseModel):
    email: EmailStr


class RegisterResponse(BaseModel):
    email: str


# 注意：本地文件仅保存一条记录，写入时覆盖
REGISTRATION_FILE = Path("data/user/registration.json")


def _ensure_registration_file():
    """确保注册文件目录存在并设置安全权限。

    契约：目录不存在时创建，并尝试设置为 `0o700`。
    副作用：创建目录、修改权限。
    失败语义：异常向上抛出并记录日志。
    """
    try:
        REGISTRATION_FILE.parent.mkdir(parents=True, exist_ok=True)
        # 注意：限制目录权限为仅 owner 读写执行
        REGISTRATION_FILE.parent.chmod(0o700)
    except Exception as e:
        logger.error(f"Failed to create registration file/directory: {e}")
        raise


# TODO：迁移到独立的服务模块


def load_registration() -> dict | None:
    """读取本地注册信息。

    契约：返回注册字典或 `None`（文件不存在/为空/损坏）。
    副作用：读取本地文件。
    失败语义：JSON 解码失败返回 `None` 并记录错误。
    """
    if not REGISTRATION_FILE.exists() or REGISTRATION_FILE.stat().st_size == 0:
        return None
    try:
        with REGISTRATION_FILE.open("rb") as f:
            content = f.read()
        return json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.error(f"Corrupted registration file: {REGISTRATION_FILE}")
        return None


def save_registration(email: str) -> bool:
    """保存注册邮箱（覆盖写入）。

    契约：成功返回 `True`。
    副作用：写入本地文件并记录日志。
    失败语义：异常向上抛出。
    """
    try:
        _ensure_registration_file()

        existing = load_registration()

        # 注意：仅保留一条注册信息，写入时覆盖
        registration = {
            "email": email,
            "registered_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        if existing:
            logger.info(f"Replacing registration: {existing.get('email')} -> {email}")

        with REGISTRATION_FILE.open("w") as f:
            json.dump(registration, f, indent=2)

        logger.info(f"Registration saved: {email}")

    except Exception as e:
        logger.error(f"Error saving registration: {e}")
        raise
    else:
        return True


@router.post("/", response_model=RegisterResponse)
async def register_user(request: RegisterRequest):
    """注册邮箱（单用户覆盖式）。

    契约：注册成功返回 `RegisterResponse`。
    副作用：写文件并发送遥测（best-effort）。
    失败语义：保存失败返回 500。

    决策：注册信息以本地单文件覆盖保存
    问题：部署场景无完整账号系统但需记录邮箱
    方案：本地 JSON 文件覆盖写入
    代价：不支持多用户与并发写入
    重评：引入账号系统或数据库时迁移存储
    """
    try:
        email = request.email
        # 注意：文件 IO 使用线程池避免阻塞事件循环
        if await to_thread(save_registration, email):
            await _send_email_telemetry(email=email)
            return RegisterResponse(email=email)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Registration failed: {e!s}") from e


async def _send_email_telemetry(email: str) -> None:
    """发送邮箱注册遥测事件（尽力而为）。"""
    payload: EmailPayload | None = None

    try:
        payload = EmailPayload(email=email)
    except ValueError as err:
        logger.error(f"Email is not a valid email address: {email}: {err}.")
        return

    logger.debug(f"Sending email telemetry event: {email}")

    telemetry_service = get_telemetry_service()

    try:
        await telemetry_service.log_package_email(payload=payload)
    except Exception as err:  # noqa: BLE001
        logger.error(f"Failed to send email telemetry event: {payload.email}: {err}")
        return

    logger.debug(f"Successfully sent email telemetry event: {payload.email}")


@router.get("/", dependencies=[Depends(get_current_active_user)])
async def get_registration():
    """获取已注册邮箱（如有）。

    契约：有注册信息时返回记录；否则返回提示消息。
    失败语义：读取失败返回 500。

    决策：无注册时返回提示消息而非 404
    问题：前端需要区分“未注册”与“请求失败”
    方案：返回固定 message
    代价：接口响应结构不完全一致
    重评：若需要统一响应结构时改为显式状态字段
    """
    try:
        registration = await to_thread(load_registration)
        if registration:
            return registration

        return {"message": "No user registered"}  # noqa: TRY300

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load registration: {e!s}") from e
