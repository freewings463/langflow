"""
模块名称：Langflow Agentic `MCP` 出口

本模块提供 `MCP` 服务对象的统一导出，主要用于 `langflow.agentic.mcp` 包的稳定导入路径。主要功能包括：
- 导出 `mcp` 实例，供 `CLI`/应用集成调用

关键组件：
- `mcp`：`FastMCP` 服务器实例

设计背景：避免上层依赖直接引用实现文件路径
注意事项：仅导出对象，不在此处启动服务
"""

from langflow.agentic.mcp.server import mcp

__all__ = ["mcp"]
