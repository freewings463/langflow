"""
模块名称：`Anthropic` 组件包

本模块提供 `Anthropic` 组件的包级入口与惰性导入逻辑，避免在非使用场景下加载依赖。
使用场景：按需访问 `AnthropicModelComponent` 等组件。
注意事项：属性访问失败会抛 `AttributeError`，并携带导入失败原因。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lfx.components._importing import import_mod

if TYPE_CHECKING:
    from lfx.components.anthropic.anthropic import AnthropicModelComponent

_dynamic_imports = {
    "AnthropicModelComponent": "anthropic",
}

__all__ = [
    "AnthropicModelComponent",
]


def __getattr__(attr_name: str) -> Any:
    """惰性导入 `Anthropic` 组件。
    契约：仅支持 `_dynamic_imports` 中声明的属性；失败抛 `AttributeError`。
    关键路径：校验属性名 → `import_mod` 动态导入 → 写回 `globals`。
    决策：使用惰性导入减少依赖加载。问题：避免未安装依赖时报错；方案：按需加载；代价：首次访问有导入开销；重评：当组件依赖稳定且需提前注册时。
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
    """返回可导出的属性列表。
    契约：返回 `__all__` 的副本列表。
    关键路径：直接转换 `__all__`。
    决策：保持 dir() 输出与 __all__ 一致。问题：保证可见性一致；方案：返回 __all__；代价：隐藏动态属性；重评：当需要暴露更多属性时。
    """
    return list(__all__)
