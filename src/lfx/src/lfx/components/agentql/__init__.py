"""
模块名称：lfx.components.agentql

本模块提供 AgentQL 组件的公开导出，用于对外保持稳定的导入路径。
主要功能包括：
- 统一导出 `AgentQL` 组件

关键组件：
- `AgentQL`：Web 数据抽取组件

设计背景：组件按子包组织，需为上层提供固定入口
使用场景：在组件注册或动态加载时导入该包
注意事项：仅转发导出，不包含业务逻辑
"""

from .agentql_api import AgentQL

__all__ = ["AgentQL"]
