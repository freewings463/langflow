"""
模块名称：Supabase 组件导出入口

本模块提供 Supabase 向量存储组件的惰性导入入口，统一对外接口。
主要功能：
- 按需导出 SupabaseVectorStoreComponent；
- 避免启动期加载不必要依赖。

设计背景：组件依赖外部 SDK，按需加载降低启动成本与依赖冲突风险。
注意事项：新增组件时需同步更新 `_dynamic_imports` 与 `__all__`。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lfx.components._importing import import_mod

if TYPE_CHECKING:
    from .supabase import SupabaseVectorStoreComponent

_dynamic_imports = {
    "SupabaseVectorStoreComponent": "supabase",
}

__all__ = [
    "SupabaseVectorStoreComponent",
]


def __getattr__(attr_name: str) -> Any:
    """惰性导入 Supabase 组件

    契约：`attr_name` 必须在 `_dynamic_imports` 中；成功返回组件类并写入 `globals()`。
    关键路径：1) 校验属性名 2) 动态导入 3) 缓存结果。
    异常流：导入失败抛 `AttributeError`。
    决策：惰性导入而非全量导入
    问题：外部依赖较重且可能未安装
    方案：按需导入并缓存
    代价：首次访问存在导入延迟
    重评：当依赖稳定且性能允许时
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
    """返回模块对外公开成员列表。"""
    return list(__all__)
