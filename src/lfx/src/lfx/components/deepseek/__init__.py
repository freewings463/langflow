"""
模块名称：deepseek 组件入口

本模块负责 DeepSeek 组件的懒加载与导出，降低启动成本。
主要功能包括：
- 功能1：按需导入 `DeepSeekModelComponent`。

使用场景：流程中需要调用 DeepSeek 模型时。
关键组件：
- 函数 `__getattr__`：属性访问触发懒加载。

设计背景：组件依赖可能较重，延迟导入减少启动开销。
注意事项：未注册的属性访问会抛 `AttributeError`。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lfx.components._importing import import_mod

if TYPE_CHECKING:
    from .deepseek import DeepSeekModelComponent

_dynamic_imports = {
    "DeepSeekModelComponent": "deepseek",
}

__all__ = [
    "DeepSeekModelComponent",
]


def __getattr__(attr_name: str) -> Any:
    """按需懒加载 DeepSeek 组件。

    契约：仅允许 `_dynamic_imports` 中声明的名称；成功后缓存到 `globals()`。
    关键路径：校验名称 -> `import_mod` 导入 -> 写入缓存。
    异常流：模块不存在或导入失败抛 `AttributeError`。
    决策：
    问题：直接导入会触发不必要依赖加载。
    方案：通过 `__getattr__` 延迟导入。
    代价：首次访问时存在导入延迟。
    重评：当启动性能不再敏感时。
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
    """返回可见导出项列表。

    契约：返回 `__all__` 中定义的名称。
    关键路径：复制 `__all__`。
    决策：
    问题：懒加载下 `dir()` 需反映公开 API。
    方案：直接返回 `__all__`。
    代价：动态导出项不会显示。
    重评：当导出项需要动态生成时。
    """
    return list(__all__)
