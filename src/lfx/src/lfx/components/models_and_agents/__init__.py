"""
模块名称：Models & Agents 组件导出入口

本模块负责按需导出模型与代理组件，降低导入开销并保持公共接口稳定。
主要功能：
- 提供组件类的惰性导入；
- 统一管理对外暴露的组件名称。

设计背景：组件较多且部分依赖较重，使用惰性导入避免启动时加载成本。
注意事项：新增组件需同时更新 `_dynamic_imports` 与 `__all__`。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lfx.components._importing import import_mod

if TYPE_CHECKING:
    from lfx.components.models_and_agents.agent import AgentComponent
    from lfx.components.models_and_agents.embedding_model import EmbeddingModelComponent
    from lfx.components.models_and_agents.language_model import LanguageModelComponent
    from lfx.components.models_and_agents.mcp_component import MCPToolsComponent
    from lfx.components.models_and_agents.memory import MemoryComponent
    from lfx.components.models_and_agents.prompt import PromptComponent

_dynamic_imports = {
    "AgentComponent": "agent",
    "EmbeddingModelComponent": "embedding_model",
    "LanguageModelComponent": "language_model",
    "MCPToolsComponent": "mcp_component",
    "MemoryComponent": "memory",
    "PromptComponent": "prompt",
}

__all__ = [
    "AgentComponent",
    "EmbeddingModelComponent",
    "LanguageModelComponent",
    "MCPToolsComponent",
    "MemoryComponent",
    "PromptComponent",
]


def __getattr__(attr_name: str) -> Any:
    """惰性导入组件属性

    契约：`attr_name` 必须在 `_dynamic_imports` 中；成功返回组件类并缓存到模块全局。
    关键路径：1) 校验属性名 2) 动态导入 3) 写入 `globals()` 缓存。
    异常流：导入失败抛 `AttributeError`，调用方应视为组件不可用。
    决策：采用惰性导入而非在模块加载时全量导入
    问题：全量导入导致启动慢且依赖链复杂
    方案：按属性访问时导入并缓存
    代价：首次访问有导入延迟
    重评：当组件数量稳定且启动性能不再敏感时
    """
    if attr_name not in _dynamic_imports:
        msg = f"module '{__name__}' has no attribute '{attr_name}'"
        raise AttributeError(msg)
    try:
        result = import_mod(attr_name, _dynamic_imports[attr_name], __spec__.parent)
    except (ModuleNotFoundError, ImportError, AttributeError) as e:
        msg = f"Could not import '{attr_name}' from '{__name__}': {e}"
        raise AttributeError(msg) from e
    globals()[attr_name] = result
    return result


def __dir__() -> list[str]:
    """返回模块可见的公开成员列表。"""
    return list(__all__)
