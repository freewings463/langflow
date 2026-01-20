"""
模块名称：Prototypes 组件导出入口

本模块提供原型组件的惰性导入入口，用于减少启动期加载成本并统一对外接口。
主要功能：
- 按需导出原型组件类；
- 维护对外可见的组件名列表。

设计背景：原型组件处于实验阶段，按需加载降低依赖耦合。
注意事项：新增组件时需同步更新 `_dynamic_imports` 与 `__all__`。
"""

from __future__ import annotations

from typing import Any

from lfx.components._importing import import_mod

# _dynamic_imports = {
#     "KnowledgeIngestionComponent": "ingestion",
#     "KnowledgeRetrievalComponent": "retrieval",
# }
_dynamic_imports = {
    "PythonFunctionComponent": "python_function",
}

# __all__ = ["KnowledgeIngestionComponent", "KnowledgeRetrievalComponent"]
__all__ = ["PythonFunctionComponent"]


def __getattr__(attr_name: str) -> Any:
    """惰性导入原型组件

    契约：`attr_name` 必须存在于 `_dynamic_imports`；成功返回组件类并缓存到模块作用域。
    关键路径：1) 校验属性名 2) 动态导入 3) 写入 `globals()`。
    异常流：导入失败抛 `AttributeError`，调用方应视为组件不可用。
    决策：使用惰性导入而非全量导入
    问题：原型组件依赖不稳定，启动期全量加载风险高
    方案：按访问触发加载并缓存
    代价：首次访问存在导入延迟
    重评：当组件稳定并需要更快首次访问时
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
