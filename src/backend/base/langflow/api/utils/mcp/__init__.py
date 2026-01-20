"""
模块名称：`MCP` 工具导出

本模块集中导出 `MCP` 配置与 `URL` 构建相关的公共接口，供 `API` 层统一引用。
主要功能包括：`Starter Projects` 自动配置、项目/`Composer` 连接地址构建、跨平台适配。

关键组件：`auto_configure_starter_projects_mcp` / `get_project_*_url` / `get_url_by_os`
设计背景：`MCP` 相关函数分散在子模块，需要统一出口避免重复导入路径。
使用场景：`API` 路由层或服务初始化阶段统一引用。
注意事项：仅做符号导出，不包含业务逻辑。
"""

from langflow.api.utils.mcp.config_utils import (
    auto_configure_starter_projects_mcp,
    get_composer_streamable_http_url,
    get_project_sse_url,
    get_project_streamable_http_url,
    get_url_by_os,
)

__all__ = [
    "auto_configure_starter_projects_mcp",
    "get_composer_streamable_http_url",
    "get_project_sse_url",
    "get_project_streamable_http_url",
    "get_url_by_os",
]
