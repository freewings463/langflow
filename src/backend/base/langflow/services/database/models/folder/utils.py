"""
模块名称：文件夹工具函数

本模块提供默认文件夹的创建与查询逻辑。
主要功能包括：为用户创建默认文件夹并绑定无归属流程。

关键组件：`create_default_folder_if_it_doesnt_exist` / `get_default_folder_id`
设计背景：确保用户始终拥有默认文件夹与有效归属。
使用场景：用户注册、登录或数据迁移时初始化目录。
注意事项：创建默认文件夹会更新未归属 `Flow` 的 `folder_id`。
"""

from uuid import UUID

from sqlmodel import and_, select, update
from sqlmodel.ext.asyncio.session import AsyncSession

from langflow.initial_setup.setup import get_or_create_default_folder
from langflow.services.database.models.flow.model import Flow

from .constants import DEFAULT_FOLDER_DESCRIPTION, DEFAULT_FOLDER_NAME
from .model import Folder


async def create_default_folder_if_it_doesnt_exist(session: AsyncSession, user_id: UUID):
    """为用户创建默认文件夹并迁移无归属流程。

    契约：
    - 输入：`session` 与 `user_id`。
    - 输出：默认 `Folder` 对象。
    - 副作用：创建文件夹并更新 `Flow.folder_id`。
    - 失败语义：数据库异常透传。

    关键路径：
    1) 查询用户是否已有文件夹。
    2) 若无则创建默认文件夹。
    3) 将无归属 `Flow` 绑定到默认文件夹。

    决策：默认文件夹作为兜底归属。
    问题：无归属流程会导致列表与权限混乱。
    方案：创建默认文件夹并迁移相关流程。
    代价：可能改变流程的原始归属语义。
    重评：当引入多根目录或标签体系时调整迁移策略。
    """
    stmt = select(Folder).where(Folder.user_id == user_id)
    folder = (await session.exec(stmt)).first()
    if not folder:
        folder = Folder(
            name=DEFAULT_FOLDER_NAME,
            user_id=user_id,
            description=DEFAULT_FOLDER_DESCRIPTION,
        )
        session.add(folder)
        await session.flush()
        await session.refresh(folder)
        await session.exec(
            update(Flow)
            .where(
                and_(
                    Flow.folder_id is None,
                    Flow.user_id == user_id,
                )
            )
            .values(folder_id=folder.id)
        )
    return folder


async def get_default_folder_id(session: AsyncSession, user_id: UUID):
    """获取用户默认文件夹 `ID`。

    契约：
    - 输入：`session` 与 `user_id`。
    - 输出：默认文件夹 `ID`。
    - 副作用：若不存在则创建默认文件夹。
    - 失败语义：数据库异常透传。

    关键路径：
    1) 按名称查询默认文件夹。
    2) 不存在时调用 `get_or_create_default_folder`。

    决策：缺失时自动创建默认文件夹。
    问题：调用方需要稳定的默认目录引用。
    方案：查询失败则创建并返回 `ID`。
    代价：读操作可能触发写入。
    重评：当目录创建迁移到注册流程时移除该行为。
    """
    folder = (
        await session.exec(select(Folder).where(Folder.name == DEFAULT_FOLDER_NAME, Folder.user_id == user_id))
    ).first()
    if not folder:
        folder = await get_or_create_default_folder(session, user_id)
    return folder.id
