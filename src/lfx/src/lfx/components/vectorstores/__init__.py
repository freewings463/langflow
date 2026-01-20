"""模块名称：向量库组件导出层

本模块负责按需导入向量库相关组件，减少启动开销并避免循环依赖。
主要功能包括：维护动态导入映射、实现懒加载、暴露 `__all__`。

关键组件：
- `_dynamic_imports`：组件名到模块名的映射
- `__getattr__`：按需导入实现

设计背景：向量库依赖较重，需要延迟加载。
注意事项：访问未注册属性会抛 `AttributeError`。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lfx.components._importing import import_mod

if TYPE_CHECKING:
    from .local_db import LocalDBComponent

_dynamic_imports = {
    "LocalDBComponent": "local_db",
}

__all__ = [
    "LocalDBComponent",
]


def __getattr__(attr_name: str) -> Any:
    """按需导入向量库组件并缓存。

    契约：输入属性名；输出组件对象；副作用：写入 `globals()`；
    失败语义：不在映射表或导入失败时抛 `AttributeError`。
    关键路径：1) 校验映射 2) 动态导入 3) 缓存并返回。
    决策：使用 `import_mod` 统一导入异常格式
    问题：需要稳定的错误提示与模块解析
    方案：复用组件导入工具
    代价：增加一层封装依赖
    重评：当导入路径稳定且无需特殊处理时改为标准库
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
    """返回模块可用属性列表。

    契约：输入无；输出 `__all__` 列表副本；副作用无；失败语义：无。
    关键路径：1) 直接返回 `__all__`。
    决策：以 `__all__` 为唯一来源
    问题：保证导出列表与文档一致
    方案：集中维护 `__all__`
    代价：新增组件需同步更新
    重评：当引入自动注册机制时由注册表生成
    """
    return list(__all__)
