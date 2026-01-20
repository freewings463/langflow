"""
模块名称：cleanlab 组件入口

本模块负责懒加载 Cleanlab 相关组件，减少非必要依赖加载成本。
主要功能包括：
- 功能1：提供 `CleanlabEvaluator`/`CleanlabRAGEvaluator`/`CleanlabRemediator` 的延迟导入。

使用场景：在运行时按需启用 Cleanlab 组件能力。
关键组件：
- 函数 `__getattr__`：按名称触发懒加载。

设计背景：Cleanlab 依赖可能较重，延迟导入可减少启动开销。
注意事项：属性不存在时会抛 `AttributeError`，需由调用方处理。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lfx.components._importing import import_mod

if TYPE_CHECKING:
    from .cleanlab_evaluator import CleanlabEvaluator
    from .cleanlab_rag_evaluator import CleanlabRAGEvaluator
    from .cleanlab_remediator import CleanlabRemediator

_dynamic_imports = {
    "CleanlabEvaluator": "cleanlab_evaluator",
    "CleanlabRAGEvaluator": "cleanlab_rag_evaluator",
    "CleanlabRemediator": "cleanlab_remediator",
}

__all__ = [
    "CleanlabEvaluator",
    "CleanlabRAGEvaluator",
    "CleanlabRemediator",
]


def __getattr__(attr_name: str) -> Any:
    """按需懒加载 Cleanlab 组件。

    契约：仅允许 `_dynamic_imports` 中声明的名称；返回对应对象。
    关键路径：校验名称 -> 调用 `import_mod` -> 缓存到 `globals()`。
    异常流：模块不存在或导入失败时抛 `AttributeError`。
    决策：
    问题：直接导入会导致无用依赖在启动时被加载。
    方案：通过 `__getattr__` 实现懒加载。
    代价：首次访问时存在导入延迟。
    重评：当组件依赖全部内置或启动性能不再敏感时。
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

    契约：返回 `__all__` 中声明的名称。
    关键路径：直接复制 `__all__`。
    决策：
    问题：懒加载下 `dir()` 需要稳定的可见接口。
    方案：返回 `__all__` 作为公开 API 列表。
    代价：未包含动态生成的符号。
    重评：当导出项改为动态生成时。
    """
    return list(__all__)
