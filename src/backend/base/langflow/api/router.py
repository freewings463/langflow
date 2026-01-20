"""
模块名称：`API` 路由聚合

本模块汇总 `v1`/`v2` 路由并挂载统一前缀，主要用于对外暴露稳定入口。主要功能包括：
- 组装 `v1` 版本路由集合
- 组装 `v2` 版本路由集合
- 提供 `/api` 统一入口

关键组件：
- router_v1 / router_v2：版本路由聚合
- router：统一入口路由

设计背景：版本化 `API` 需要统一入口，便于路由管理与兼容演进。
注意事项：新增版本需同步更新此聚合模块。
"""

from fastapi import APIRouter

from langflow.api.v1 import (
    api_key_router,
    chat_router,
    endpoints_router,
    files_router,
    flows_router,
    folders_router,
    knowledge_bases_router,
    login_router,
    mcp_projects_router,
    mcp_router,
    model_options_router,
    models_router,
    monitor_router,
    openai_responses_router,
    projects_router,
    starter_projects_router,
    store_router,
    users_router,
    validate_router,
    variables_router,
)
from langflow.api.v1.voice_mode import router as voice_mode_router
from langflow.api.v2 import files_router as files_router_v2
from langflow.api.v2 import mcp_router as mcp_router_v2
from langflow.api.v2 import registration_router as registration_router_v2
from langflow.api.v2 import workflow_router as workflow_router_v2

router_v1 = APIRouter(prefix="/v1")

router_v2 = APIRouter(prefix="/v2")

router_v1.include_router(chat_router)
router_v1.include_router(endpoints_router)
router_v1.include_router(validate_router)
router_v1.include_router(store_router)
router_v1.include_router(flows_router)
router_v1.include_router(users_router)
router_v1.include_router(api_key_router)
router_v1.include_router(login_router)
router_v1.include_router(variables_router)
router_v1.include_router(files_router)
router_v1.include_router(monitor_router)
router_v1.include_router(folders_router)
router_v1.include_router(projects_router)
router_v1.include_router(starter_projects_router)
router_v1.include_router(knowledge_bases_router)
router_v1.include_router(mcp_router)
router_v1.include_router(voice_mode_router)
router_v1.include_router(mcp_projects_router)
router_v1.include_router(openai_responses_router)
router_v1.include_router(models_router)
router_v1.include_router(model_options_router)

router_v2.include_router(files_router_v2)
router_v2.include_router(mcp_router_v2)
router_v2.include_router(registration_router_v2)
router_v2.include_router(workflow_router_v2)

router = APIRouter(
    prefix="/api",
)
router.include_router(router_v1)
router.include_router(router_v2)
