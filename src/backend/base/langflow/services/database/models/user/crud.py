"""
模块名称：用户数据访问

本模块提供用户查询与更新相关操作。
主要功能包括：按用户名/ID 查询、更新用户信息与更新登录时间。

关键组件：`get_user_by_username` / `update_user`
设计背景：集中处理用户更新逻辑与错误语义。
使用场景：登录、个人资料更新与后台管理。
注意事项：更新为空时返回 `HTTP 304`。
"""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from lfx.log.logger import logger
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from langflow.services.database.models.user.model import User, UserUpdate


async def get_user_by_username(db: AsyncSession, username: str) -> User | None:
    """按用户名获取用户。

    契约：
    - 输入：`db` 与 `username`。
    - 输出：`User` 或 `None`。
    - 副作用：读取数据库。
    - 失败语义：查询异常透传。

    决策：使用精确匹配查询用户名。
    问题：用户名为唯一键，需快速定位用户。
    方案：按 `username` 精确查询。
    代价：不支持模糊查询。
    重评：当需要搜索功能时新增模糊查询接口。
    """
    stmt = select(User).where(User.username == username)
    return (await db.exec(stmt)).first()


async def get_user_by_id(db: AsyncSession, user_id: UUID) -> User | None:
    """按用户 `ID` 获取用户。

    契约：
    - 输入：`db` 与 `user_id`。
    - 输出：`User` 或 `None`。
    - 副作用：读取数据库。
    - 失败语义：查询异常透传。

    决策：支持字符串形式的 `UUID`。
    问题：上游可能以字符串传递 `ID`。
    方案：检测并转换为 `UUID`。
    代价：非法字符串会抛异常。
    重评：当上游类型统一后移除转换。
    """
    if isinstance(user_id, str):
        user_id = UUID(user_id)
    stmt = select(User).where(User.id == user_id)
    return (await db.exec(stmt)).first()


async def update_user(user_db: User | None, user: UserUpdate, db: AsyncSession) -> User:
    """更新用户信息。

    契约：
    - 输入：`user_db`、`user`、`db`。
    - 输出：更新后的 `User`。
    - 副作用：写入数据库并更新 `updated_at`。
    - 失败语义：用户不存在抛 `HTTPException(404)`；无变更抛 `HTTP 304`。

    关键路径：
    1) 过滤未设置字段。
    2) 应用变更并更新 `updated_at`。
    3) 刷新并处理唯一约束异常。

    决策：拒绝“无变化”更新请求。
    问题：空更新会浪费数据库写入。
    方案：无字段变更时返回 `HTTP 304`。
    代价：调用方需处理 304 语义。
    重评：当需要幂等更新时可改为直接返回原对象。
    """
    if not user_db:
        raise HTTPException(status_code=404, detail="User not found")

    # 注意：用户名冲突校验暂未启用，避免重复查询。

    user_data = user.model_dump(exclude_unset=True)
    changed = False
    for attr, value in user_data.items():
        if hasattr(user_db, attr) and value is not None:
            setattr(user_db, attr, value)
            changed = True

    if not changed:
        raise HTTPException(status_code=status.HTTP_304_NOT_MODIFIED, detail="Nothing to update")

    user_db.updated_at = datetime.now(timezone.utc)
    flag_modified(user_db, "updated_at")

    try:
        await db.flush()
    except IntegrityError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return user_db


async def update_user_last_login_at(user_id: UUID, db: AsyncSession):
    """更新用户最后登录时间。

    契约：
    - 输入：`user_id` 与 `db`。
    - 输出：更新后的 `User` 或 `None`。
    - 副作用：写入数据库。
    - 失败语义：异常时记录日志并返回 `None`。

    决策：异常仅记录日志避免阻断登录流程。
    问题：登录流程不应因写入失败而阻塞。
    方案：捕获异常并记录。
    代价：可能丢失登录时间记录。
    重评：当需要强一致审计时改为失败即终止。
    """
    try:
        user_data = UserUpdate(last_login_at=datetime.now(timezone.utc))
        user = await get_user_by_id(db, user_id)
        return await update_user(user, user_data, db)
    except Exception as e:  # noqa: BLE001
        await logger.aerror(f"Error updating user last login at: {e!s}")


async def get_all_superusers(db: AsyncSession) -> list[User]:
    """获取所有超级用户。

    契约：
    - 输入：`db`。
    - 输出：`User` 列表。
    - 副作用：读取数据库。
    - 失败语义：查询异常透传。

    决策：按 `is_superuser=True` 过滤。
    问题：需要集中管理超级用户账号。
    方案：使用布尔字段过滤。
    代价：无法区分不同级别的管理员。
    重评：当引入角色系统时改为角色表查询。
    """
    stmt = select(User).where(User.is_superuser == True)  # noqa: E712
    result = await db.exec(stmt)
    return list(result.all())
