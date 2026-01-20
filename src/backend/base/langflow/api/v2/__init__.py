"""
模块名称：V2 API 路由汇总

本模块负责汇总 V2 版本 API 的子路由并暴露给上层装配。
主要功能包括：
- 聚合 `files`/`mcp`/`registration`/`workflow` 路由
- 通过 `__all__` 控制对外暴露范围

关键组件：
- `files_router`：文件相关接口
- `mcp_router`：MCP 配置接口
- `registration_router`：注册与遥测接口
- `workflow_router`：工作流执行接口

设计背景：V2 路由按领域拆分，集中导出可减少重复导入。
注意事项：本模块只做路由聚合，不应承载业务逻辑。
"""

from .files import router as files_router
from .mcp import router as mcp_router
from .registration import router as registration_router
from .workflow import router as workflow_router

__all__ = ["files_router", "mcp_router", "registration_router", "workflow_router"]
