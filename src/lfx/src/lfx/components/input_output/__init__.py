"""
模块名称：输入/输出组件包入口

本模块提供输入输出相关组件的延迟导入入口，主要用于按需加载
Chat/Text/Webhook 组件实现。
主要功能包括：
- 通过 `__getattr__` 延迟加载组件
- 维护对外公开的组件符号列表

关键组件：
- `ChatInput` / `ChatOutput` / `TextInputComponent` / `TextOutputComponent` / `WebhookComponent`

设计背景：降低模块级导入成本并避免可选依赖在启动时失败。
注意事项：导入失败会抛 `AttributeError`，上层需处理。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lfx.components._importing import import_mod

if TYPE_CHECKING:
    from lfx.components.input_output.chat import ChatInput
    from lfx.components.input_output.chat_output import ChatOutput
    from lfx.components.input_output.text import TextInputComponent
    from lfx.components.input_output.text_output import TextOutputComponent
    from lfx.components.input_output.webhook import WebhookComponent

_dynamic_imports = {
    "ChatInput": "chat",
    "ChatOutput": "chat_output",
    "TextInputComponent": "text",
    "TextOutputComponent": "text_output",
    "WebhookComponent": "webhook",
}

__all__ = ["ChatInput", "ChatOutput", "TextInputComponent", "TextOutputComponent", "WebhookComponent"]


def __getattr__(attr_name: str) -> Any:
    """按需延迟导入输入/输出组件。

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
