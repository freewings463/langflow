"""
模块名称：`LLM` 操作组件懒加载入口

本模块提供组件的按需导入，主要用于前端节点动态加载与启动性能控制。主要功能包括：
- 将组件名映射到子模块并延迟 import
- 属性访问失败时抛出统一的 `AttributeError`
- 通过 `__all__` 和 `__dir__` 暴露稳定的发现入口

关键组件：
- `_dynamic_imports`：组件名到模块名映射表
- `__getattr__`：懒加载与缓存入口
- `__dir__`：`IDE`/反射提示一致性

设计背景：组件数量增多导致初始化成本上升，需要按需加载。
注意事项：仅支持映射表内的名字，其它访问会抛 `AttributeError`。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lfx.components._importing import import_mod

if TYPE_CHECKING:
    from lfx.components.llm_operations.batch_run import BatchRunComponent
    from lfx.components.llm_operations.lambda_filter import SmartTransformComponent
    from lfx.components.llm_operations.llm_conditional_router import SmartRouterComponent
    from lfx.components.llm_operations.llm_selector import LLMSelectorComponent
    from lfx.components.llm_operations.structured_output import StructuredOutputComponent

_dynamic_imports = {
    "BatchRunComponent": "batch_run",
    "SmartTransformComponent": "lambda_filter",
    "SmartRouterComponent": "llm_conditional_router",
    "LLMSelectorComponent": "llm_selector",
    "StructuredOutputComponent": "structured_output",
}

__all__ = [
    "BatchRunComponent",
    "LLMSelectorComponent",
    "SmartRouterComponent",
    "SmartTransformComponent",
    "StructuredOutputComponent",
]


def __getattr__(attr_name: str) -> Any:
    """按名称懒加载组件并缓存到模块全局。

    契约：输入 `attr_name` 为组件类名，返回对应类对象。
    关键路径：校验名称 -> 动态导入 -> 写入 `globals()` 缓存。
    失败语义：不存在或导入失败时抛 `AttributeError`，调用方可降级为不可用组件。
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
    """返回可见成员列表，保证反射与自动补全一致。"""
    return list(__all__)
