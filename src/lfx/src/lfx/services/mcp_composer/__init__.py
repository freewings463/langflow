"""
模块名称：MCP Composer 服务导出

模块目的：统一导出 MCP Composer 服务与工厂。
使用场景：在服务注册/依赖注入时获取 Composer 能力。
主要功能包括：
- 导出 `MCPComposerService`
- 导出 `MCPComposerServiceFactory`

设计背景：将服务实现与创建逻辑解耦，便于替换与测试。
注意：仅导出服务对象，不负责初始化配置。
"""

from lfx.services.mcp_composer.factory import MCPComposerServiceFactory
from lfx.services.mcp_composer.service import MCPComposerService

__all__ = ["MCPComposerService", "MCPComposerServiceFactory"]
