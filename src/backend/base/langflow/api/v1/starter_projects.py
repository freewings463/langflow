"""
模块名称：Starter Projects 接口

本模块提供内置示例项目的结构化返回，用于前端「新手项目」展示。
主要功能：
- 暴露示例项目列表
- 将 TypedDict 结构转换为 Pydantic 模型
设计背景：统一 API 返回结构，便于前端渲染画布。
注意事项：该接口仅对已登录用户开放。
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from langflow.services.auth.utils import get_current_active_user

router = APIRouter(prefix="/starter-projects", tags=["Flows"])


# 实现：为 API schema 提供简化的 Pydantic 模型。
class ViewPort(BaseModel):
    """前端画布视口坐标。"""

    x: float
    y: float
    zoom: float


class NodeData(BaseModel):
    """节点数据占位模型，允许透传额外字段。"""

    # 注意：仅保留基础结构，真实字段由前端自定义扩展。
    model_config = {"extra": "allow"}  # 允许额外字段。


class EdgeData(BaseModel):
    """边数据占位模型，允许透传额外字段。"""

    # 注意：仅保留基础结构，真实字段由前端自定义扩展。
    model_config = {"extra": "allow"}  # 允许额外字段。


class GraphData(BaseModel):
    """画布图数据（节点/边/视口）。"""

    nodes: list[dict[str, Any]]  # 注意：使用 `dict` 以兼容复杂的 NodeData 结构。
    edges: list[dict[str, Any]]  # 注意：使用 `dict` 以兼容复杂的 EdgeData 结构。
    viewport: ViewPort | None = None


class GraphDumpResponse(BaseModel):
    """示例项目返回结构。"""

    data: GraphData
    is_component: bool | None = None
    name: str | None = None
    description: str | None = None
    endpoint_name: str | None = None


@router.get("/", dependencies=[Depends(get_current_active_user)], status_code=200)
async def get_starter_projects() -> list[GraphDumpResponse]:
    """获取示例项目列表。

    契约：
    - 输出：`GraphDumpResponse` 列表
    - 失败语义：异常转 `HTTPException(500)`
    """
    from langflow.initial_setup.load import get_starter_projects_dump

    try:
        # 实现：读取底层 GraphDump 原始数据。
        raw_data = get_starter_projects_dump()

        # 实现：转换为 Pydantic 模型以统一 API 输出。
        results = []
        for item in raw_data:
            # 实现：组装画布结构。
            graph_data = GraphData(
                nodes=item.get("data", {}).get("nodes", []),
                edges=item.get("data", {}).get("edges", []),
                viewport=item.get("data", {}).get("viewport"),
            )

            # 实现：组装响应对象。
            graph_dump = GraphDumpResponse(
                data=graph_data,
                is_component=item.get("is_component"),
                name=item.get("name"),
                description=item.get("description"),
                endpoint_name=item.get("endpoint_name"),
            )
            results.append(graph_dump)

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return results
