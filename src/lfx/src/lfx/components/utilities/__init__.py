"""
模块名称：通用工具组件延迟加载入口

本模块提供 utilities 目录下组件的按需导入，主要用于降低启动成本并避免可选依赖缺失时报错。主要功能包括：
- 维护组件名到子模块的映射，控制导出符号
- 在属性访问时触发动态导入并缓存到模块全局
- 通过 `__dir__` 保证 IDE/自动补全可见

关键组件：
- `_dynamic_imports`：组件名到子模块名的映射
- `__getattr__`：延迟导入与错误包装
- `__dir__`：导出可见属性列表

设计背景：工具类组件数量多且互不依赖，懒加载更符合按需使用。
使用场景：仅在访问某个工具组件时才加载其实现。
注意事项：仅支持映射表内属性，导入失败会转为 `AttributeError` 并包含原异常信息。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lfx.components._importing import import_mod

if TYPE_CHECKING:
    from lfx.components.utilities.calculator_core import CalculatorComponent
    from lfx.components.utilities.current_date import CurrentDateComponent
    from lfx.components.utilities.id_generator import IDGeneratorComponent
    from lfx.components.utilities.python_repl_core import PythonREPLComponent

_dynamic_imports = {
    "CalculatorComponent": "calculator_core",
    "CurrentDateComponent": "current_date",
    "IDGeneratorComponent": "id_generator",
    "PythonREPLComponent": "python_repl_core",
}

__all__ = [
    "CalculatorComponent",
    "CurrentDateComponent",
    "IDGeneratorComponent",
    "PythonREPLComponent",
]


def __getattr__(attr_name: str) -> Any:
    """按需导入工具组件并缓存到模块全局。

    契约：仅接受 `_dynamic_imports` 中的属性名；返回被导入对象并写入 `globals()`。
    副作用：首次访问触发动态导入并缓存，后续访问直接命中缓存。
    失败语义：属性不在映射表内或导入失败时抛 `AttributeError`，错误信息包含原异常摘要。
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
    """返回模块可见属性列表，供反射与自动补全使用。"""
    return list(__all__)
