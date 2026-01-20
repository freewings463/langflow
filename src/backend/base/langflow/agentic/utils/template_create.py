"""
模块名称：模板创建流程

本模块根据 `starter template` 创建新的 `Flow`，并返回 `UI` 可访问链接，适用于一键生成示例或起步流程。主要功能包括：
- 通过模板 `ID` 读取模板 `JSON`
- 解析目标文件夹并创建 `Flow` 记录
- 复用 `API` 逻辑写入存储并返回链接

关键组件：
- `create_flow_from_template_and_get_link`：模板创建与链接返回

设计背景：复用 `API` 创建路径以保持校验与存储行为一致。
注意事项：模板不存在或文件夹无效时抛 `HTTPException`。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

from langflow.agentic.utils.template_search import get_template_by_id
from langflow.api.v1.flows import _new_flow, _save_flow_to_fs
from langflow.initial_setup.setup import get_or_create_default_folder
from langflow.services.database.models.flow.model import FlowCreate
from langflow.services.database.models.folder.model import Folder
from langflow.services.deps import get_storage_service

if TYPE_CHECKING:
    from uuid import UUID

    from sqlmodel.ext.asyncio.session import AsyncSession


async def create_flow_from_template_and_get_link(
    *,
    session: AsyncSession,
    user_id: UUID,
    template_id: str,
    target_folder_id: UUID | None = None,
) -> dict[str, Any]:
    """从模板创建 `Flow` 并返回 `{id, link}`。

    契约：`template_id` 必须命中模板；`user_id` 为新 `Flow` 归属；返回含 `id` 与 `link`。
    副作用：写入数据库并通过 `_save_flow_to_fs` 保存到存储。
    失败语义：模板不存在 -> 抛 `HTTPException(404)`；目标文件夹无效 -> 抛 `HTTPException(400)`。
    关键路径（三步）：1) 读取模板 2) 解析目标文件夹 3) 复用 `API` 创建并持久化
    异常流：DB/存储异常将向上抛出，需由上层处理事务与日志。
    性能瓶颈：保存流程数据与文件写入取决于模板体积。
    排障入口：异常 `detail` 为 `Template not found`/`Invalid target folder`。
    决策：复用 `API` 创建路径而非手工插表
    问题：手工写入容易绕过统一校验与存储逻辑
    方案：调用 `_new_flow` 与 `_save_flow_to_fs`
    代价：依赖 `API` 内部约定与参数结构
    重评：当 `API` 创建流程变化或拆分为独立服务时
    """
    template = get_template_by_id(template_id=template_id, fields=None)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    if target_folder_id:
        folder = await session.get(Folder, target_folder_id)
        if not folder or folder.user_id != user_id:
            raise HTTPException(status_code=400, detail="Invalid target folder")
        folder_id = folder.id
    else:
        default_folder = await get_or_create_default_folder(session, user_id)
        folder_id = default_folder.id

    new_flow = FlowCreate(
        name=template.get("name"),
        description=template.get("description"),
        icon=template.get("icon"),
        icon_bg_color=template.get("icon_bg_color"),
        gradient=template.get("gradient"),
        data=template.get("data"),
        is_component=template.get("is_component", False),
        endpoint_name=template.get("endpoint_name"),
        tags=template.get("tags"),
        mcp_enabled=template.get("mcp_enabled"),
        folder_id=folder_id,
        user_id=user_id,
    )

    storage_service = get_storage_service()
    db_flow = await _new_flow(session=session, flow=new_flow, user_id=user_id, storage_service=storage_service)
    await session.commit()
    await session.refresh(db_flow)
    await _save_flow_to_fs(db_flow, user_id, storage_service)

    link = f"/flow/{db_flow.id}/folder/{folder_id}"
    return {"id": str(db_flow.id), "link": link}
