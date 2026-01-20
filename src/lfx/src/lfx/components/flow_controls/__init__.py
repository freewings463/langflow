"""
模块名称：流程控制组件包入口

本模块提供流程控制相关组件的延迟导入入口，主要用于在可选依赖存在时
按需加载组件实现。
主要功能包括：
- 通过 `__getattr__` 延迟加载组件
- 维护对外公开的组件符号列表

关键组件：
- `ConditionalRouterComponent` / `LoopComponent` / `RunFlowComponent` 等

设计背景：减少模块级导入成本并避免可选依赖在启动时失败。
注意事项：导入失败会抛 `AttributeError`，上层需处理。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lfx.components._importing import import_mod

if TYPE_CHECKING:
    from lfx.components.flow_controls.conditional_router import ConditionalRouterComponent
    from lfx.components.flow_controls.data_conditional_router import DataConditionalRouterComponent
    from lfx.components.flow_controls.flow_tool import FlowToolComponent
    from lfx.components.flow_controls.listen import ListenComponent
    from lfx.components.flow_controls.loop import LoopComponent
    from lfx.components.flow_controls.notify import NotifyComponent
    from lfx.components.flow_controls.pass_message import PassMessageComponent
    from lfx.components.flow_controls.run_flow import RunFlowComponent
    from lfx.components.flow_controls.sub_flow import SubFlowComponent

_dynamic_imports = {
    "ConditionalRouterComponent": "conditional_router",
    "DataConditionalRouterComponent": "data_conditional_router",
    "FlowToolComponent": "flow_tool",
    "ListenComponent": "listen",
    "LoopComponent": "loop",
    "NotifyComponent": "notify",
    "PassMessageComponent": "pass_message",
    "RunFlowComponent": "run_flow",
    "SubFlowComponent": "sub_flow",
}

__all__ = [
    "ConditionalRouterComponent",
    "DataConditionalRouterComponent",
    "FlowToolComponent",
    "ListenComponent",
    "LoopComponent",
    "NotifyComponent",
    "PassMessageComponent",
    "RunFlowComponent",
    "SubFlowComponent",
]


def __getattr__(attr_name: str) -> Any:
    """按需延迟导入流程控制组件。

    契约：仅允许 `_dynamic_imports` 中声明的组件被访问。
    失败语义：模块缺失或导入失败会抛 `AttributeError`。
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
    """返回对外公开的组件符号列表。"""
    return list(__all__)
