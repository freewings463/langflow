"""
模块名称：CrewAI 组件导出

本模块提供 CrewAI 组件的延迟导入入口，按需加载相关组件。
注意事项：新增组件需同步更新 `__all__` 与 `_dynamic_imports`。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lfx.components._importing import import_mod

if TYPE_CHECKING:
    from .crewai import CrewAIAgentComponent
    from .hierarchical_crew import HierarchicalCrewComponent
    from .hierarchical_task import HierarchicalTaskComponent
    from .sequential_crew import SequentialCrewComponent
    from .sequential_task import SequentialTaskComponent
    from .sequential_task_agent import SequentialTaskAgentComponent

_dynamic_imports = {
    "CrewAIAgentComponent": "crewai",
    "HierarchicalCrewComponent": "hierarchical_crew",
    "HierarchicalTaskComponent": "hierarchical_task",
    "SequentialCrewComponent": "sequential_crew",
    "SequentialTaskAgentComponent": "sequential_task_agent",
    "SequentialTaskComponent": "sequential_task",
}

__all__ = [
    "CrewAIAgentComponent",
    "HierarchicalCrewComponent",
    "HierarchicalTaskComponent",
    "SequentialCrewComponent",
    "SequentialTaskAgentComponent",
    "SequentialTaskComponent",
]


def __getattr__(attr_name: str) -> Any:
    """按需导入 CrewAI 组件。

    契约：输入属性名，输出对应组件对象。
    副作用：首次访问会执行动态导入并缓存到模块全局。
    失败语义：未注册属性抛 `AttributeError`。
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
    """返回模块可用的导出项列表。"""
    return list(__all__)
