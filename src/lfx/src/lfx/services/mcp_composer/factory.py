"""
模块名称：MCP Composer 服务工厂

模块目的：提供 MCP Composer 服务实例化入口。
使用场景：在服务注册或测试中按需创建服务实例。
主要功能包括：
- 设置 `service_class`
- 产出 `MCPComposerService` 实例

设计背景：与 ServiceFactory 体系保持一致。
注意：当前 `create` 忽略传入参数，后续扩展需同步接口。
"""

from lfx.services.factory import ServiceFactory
from lfx.services.mcp_composer.service import MCPComposerService


class MCPComposerServiceFactory(ServiceFactory):
    """MCP Composer 服务工厂。"""

    def __init__(self):
        """初始化工厂并绑定服务类。"""
        super().__init__()
        self.service_class = MCPComposerService

    def create(self, **kwargs):  # noqa: ARG002
        """创建 MCP Composer 服务实例。"""
        return MCPComposerService()
