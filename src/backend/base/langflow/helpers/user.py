"""
模块名称：用户查询辅助

本模块提供通过 Flow 标识获取用户信息的能力。
主要功能包括：
- 通过 Flow ID 或 endpoint_name 获取用户

关键组件：
- `get_user_by_flow_id_or_endpoint_name`

设计背景：部分 API 仅持有 Flow 标识，需要反查所属用户。
注意事项：未找到 Flow 或 User 会返回 404。
"""

from uuid import UUID

from fastapi import HTTPException
from lfx.services.deps import session_scope_readonly
from sqlmodel import select

from langflow.services.database.models.flow.model import Flow
from langflow.services.database.models.user.model import User, UserRead


async def get_user_by_flow_id_or_endpoint_name(flow_id_or_name: str) -> UserRead | None:
    """通过 Flow 标识获取所属用户。

    契约：返回 `UserRead`，找不到 Flow/User 时抛 404。
    失败语义：Flow 或 User 不存在抛 `HTTPException(404)`。

    决策：优先按 UUID 解析 Flow ID
    问题：同一参数需要支持 ID 与 endpoint_name
    方案：先尝试 UUID，失败后按 endpoint 查询
    代价：ID 解析失败会走异常路径
    重评：若引入显式前缀区分可避免异常控制流
    """
    async with session_scope_readonly() as session:
        try:
            flow_id = UUID(flow_id_or_name)
            flow = await session.get(Flow, flow_id)
        except ValueError:
            stmt = select(Flow).where(Flow.endpoint_name == flow_id_or_name)
            flow = (await session.exec(stmt)).first()

        if flow is None:
            raise HTTPException(status_code=404, detail=f"Flow identifier {flow_id_or_name} not found")

        user = await session.get(User, flow.user_id)
        if user is None:
            raise HTTPException(status_code=404, detail=f"User for flow {flow_id_or_name} not found")

        return UserRead.model_validate(user, from_attributes=True)
