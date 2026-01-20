from __future__ import annotations

"""
模块名称：MongoDB 组件懒加载入口

本模块负责对 MongoDB 相关组件进行延迟导入，避免在未使用时触发可选依赖加载。
主要功能包括：
- 统一暴露 `MongoVectorStoreComponent`
- 属性访问时按需加载子模块并写回缓存

关键组件：
- `__getattr__`：动态导入与异常封装
- `__dir__`：向补全系统暴露公开符号

设计背景：向量库依赖较重且可能缺失，需延迟加载。
注意事项：不在白名单的属性将抛 `AttributeError`。
"""

from typing import TYPE_CHECKING, Any

from lfx.components._importing import import_mod

if TYPE_CHECKING:
    from .mongodb_atlas import MongoVectorStoreComponent

_dynamic_imports = {
    "MongoVectorStoreComponent": "mongodb_atlas",
}

__all__ = [
    "MongoVectorStoreComponent",
]


def __getattr__(attr_name: str) -> Any:
    """按属性名延迟导入组件。

    契约：
    - 输入：`attr_name` 必须位于 `__all__`
    - 输出：返回对应类对象，并写入 `globals()` 作为缓存
    - 副作用：触发模块导入
    - 失败语义：导入失败抛 `AttributeError`，包含原始异常信息
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
    """返回公开符号列表，供 `dir()` 与 IDE 补全使用。"""
    return list(__all__)
