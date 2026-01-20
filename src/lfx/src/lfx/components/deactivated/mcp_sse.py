"""
模块名称：MCP SSE 工具组件（已停用）

本模块提供通过 SSE 连接 MCP 服务器并暴露工具列表的组件，主要用于让 Agent 调用外部 MCP 工具。主要功能包括：
- 建立 SSE 连接并拉取工具定义
- 将 MCP 工具转换为 LangChain `StructuredTool`

关键组件：
- `MCPSse`：SSE 工具组件

设计背景：用于早期 MCP 集成方案，现标记为 legacy。
注意事项：需保证 MCP 服务端可用；工具 schema 需符合 MCP 规范。
"""

from langchain_core.tools import StructuredTool
from mcp import types

from lfx.base.mcp.util import (
    MCPSseClient,
    create_input_schema_from_json_schema,
    create_tool_coroutine,
    create_tool_func,
)
from lfx.custom.custom_component.component import Component
from lfx.field_typing import Tool
from lfx.io import MessageTextInput, Output


class MCPSse(Component):
    """MCP SSE 工具组件。

    契约：连接 MCP SSE 服务并返回 `Tool` 列表。
    失败语义：连接失败或 schema 解析失败时抛异常。
    副作用：建立网络连接并缓存会话。
    """
    client = MCPSseClient()
    tools = types.ListToolsResult
    tool_names = [str]
    display_name = "MCP Tools (SSE) [DEPRECATED]"
    description = "Connects to an MCP server over SSE and exposes it's tools as langflow tools to be used by an Agent."
    documentation: str = "https://docs.langflow.org/components-custom-components"
    icon = "code"
    name = "MCPSse"
    legacy = True

    inputs = [
        MessageTextInput(
            name="url",
            display_name="mcp sse url",
            info="sse url",
            value="http://localhost:7860/api/v1/mcp/sse",
            tool_mode=True,
        ),
    ]

    outputs = [
        Output(display_name="Tools", name="tools", method="build_output"),
    ]

    async def build_output(self) -> list[Tool]:
        """构建 LangChain 工具列表。

        契约：每个 MCP 工具转换为 `StructuredTool` 并返回列表。
        失败语义：连接失败或工具 schema 不合法时抛异常。
        副作用：可能创建 MCP 会话并更新 `tool_names`。

        关键路径（三步）：
        1) 建立 SSE 连接并获取工具列表
        2) 将 MCP schema 转为输入 schema
        3) 构建 `StructuredTool` 并返回
        """
        if self.client.session is None:
            self.tools = await self.client.connect_to_server(self.url, {})

        tool_list = []

        for tool in self.tools:
            args_schema = create_input_schema_from_json_schema(tool.inputSchema)
            tool_list.append(
                StructuredTool(
                    name=tool.name,  # 注意：名称可按需格式化
                    description=tool.description,
                    args_schema=args_schema,
                    func=create_tool_func(tool.name, args_schema, self.client.session),
                    coroutine=create_tool_coroutine(tool.name, args_schema, self.client.session),
                )
            )

        self.tool_names = [tool.name for tool in self.tools]
        return tool_list
