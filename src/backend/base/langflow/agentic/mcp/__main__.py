"""
模块名称：`MCP` 服务启动入口

本模块用于通过 `python -m langflow.agentic.mcp` 启动 `MCP` 服务。主要功能包括：
- 导入 `mcp` 并调用 `run()` 进入服务循环

关键组件：
- `mcp`：`FastMCP` 服务器实例

设计背景：提供标准模块执行入口，便于脚本化部署
注意事项：`run()` 会阻塞当前进程；异常原样向上抛出
"""

from langflow.agentic.mcp.server import mcp

if __name__ == "__main__":
    mcp.run()
