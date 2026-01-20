"""
模块名称：V2 Workflow 执行接口

本模块提供工作流执行、状态查询与停止的 API 入口，并对开发者开关进行保护。
主要功能包括：
- 通过 API Key 执行工作流（sync/stream/background）
- 查询执行状态与结果
- 停止运行中的任务

关键组件：
- `check_developer_api_enabled`：开发者开关校验
- `execute_workflow`：执行入口（待实现）
- `get_workflow_status` / `stop_workflow`：状态与停止（待实现）

设计背景：开发者 API 仅面向受控用户，默认关闭以避免暴露执行能力。
注意事项：当前接口返回 501，完成实现后再对外开放。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from lfx.schema.workflow import (
    WORKFLOW_EXECUTION_RESPONSES,
    WORKFLOW_STATUS_RESPONSES,
    WorkflowExecutionRequest,
    WorkflowExecutionResponse,
    WorkflowJobResponse,
    WorkflowStopRequest,
    WorkflowStopResponse,
)
from lfx.services.deps import get_settings_service

from langflow.helpers.flow import get_flow_by_id_or_endpoint_name
from langflow.services.auth.utils import api_key_security
from langflow.services.database.models.user.model import UserRead


def check_developer_api_enabled() -> None:
    """校验开发者 API 开关。

    契约：开关关闭时抛 `HTTPException(404)`。
    副作用：读取配置。
    失败语义：`developer_api_enabled=False` 时返回 404。

    决策：返回 404 而非 403/401
    问题：避免暴露端点存在与否
    方案：以 404 隐藏未启用的开发者接口
    代价：真实权限问题被“未启用”掩盖
    重评：需要区分权限与未启用时改为 403
    """
    settings = get_settings_service().settings
    if not settings.developer_api_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This endpoint is not available",
        )


router = APIRouter(prefix="/workflow", tags=["Workflow"], dependencies=[Depends(check_developer_api_enabled)])


@router.post(
    "",
    response_model=None,
    response_model_exclude_none=True,
    responses=WORKFLOW_EXECUTION_RESPONSES,
    summary="Execute Workflow",
    description="Execute a workflow with support for sync, stream, and background modes",
)
async def execute_workflow(
    workflow_request: WorkflowExecutionRequest,
    background_tasks: BackgroundTasks,  # noqa: ARG001
    api_key_user: Annotated[UserRead, Depends(api_key_security)],
) -> WorkflowExecutionResponse | WorkflowJobResponse | StreamingResponse:
    """执行工作流（支持同步、流式、后台三种模式）。

    契约：根据请求参数返回执行结果/任务信息/流式响应。
    关键路径（三步）：
    1) 解析 `flow_id` 并校验访问
    2) 选择执行模式并调度
    3) 返回结果或任务句柄
    失败语义：流程不存在返回 404；当前实现返回 501。

    决策：单一入口支持三种执行模式
    问题：调用方需要灵活选择执行方式
    方案：用请求参数区分 sync/stream/background
    代价：接口逻辑复杂度提升
    重评：若后续拆分独立端点再调整
    """
    flow = await get_flow_by_id_or_endpoint_name(workflow_request.flow_id, api_key_user.id)
    if not flow:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Flow {workflow_request.flow_id} not found")

    # TODO：待实现工作流执行
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="Not implemented /workflow execution yet")


@router.get(
    "",
    response_model=None,
    response_model_exclude_none=True,
    responses=WORKFLOW_STATUS_RESPONSES,
    summary="Get Workflow Status",
    description="Get status of workflow job by job ID",
)
async def get_workflow_status(
    api_key_user: Annotated[UserRead, Depends(api_key_security)],  # noqa: ARG001
    job_id: Annotated[str, Query(description="Job ID to query")],  # noqa: ARG001
) -> WorkflowExecutionResponse | StreamingResponse:
    """按任务 ID 查询工作流状态与结果。

    契约：返回任务状态或结果。
    失败语义：当前实现返回 501。

    决策：独立状态查询接口
    问题：后台任务需要异步查询
    方案：提供 `/workflow` 状态查询入口
    代价：需要维护任务状态存储
    重评：若引入统一作业中心可合并
    """
    # TODO：待实现状态查询
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="Not implemented /status yet")


@router.post(
    "/stop",
    summary="Stop Workflow",
    description="Stop a running workflow execution",
)
async def stop_workflow(
    request: WorkflowStopRequest,  # noqa: ARG001
    api_key_user: Annotated[UserRead, Depends(api_key_security)],  # noqa: ARG001
) -> WorkflowStopResponse:
    """停止运行中的工作流任务。

    契约：输入 `job_id` 与可选 `force`，返回停止结果。
    失败语义：当前实现返回 501。

    决策：支持 `force` 强制停止参数
    问题：任务可能无法正常收敛
    方案：暴露强制终止选项
    代价：强制终止可能导致中间状态不一致
    重评：当有更安全的取消机制时再收敛
    """
    # TODO：待实现停止逻辑
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="Not implemented /stop yet")
