"""模块名称：前端节点模板导出入口

本模块负责聚合 `frontend_node` 子模块并暴露给上层调用。
主要功能包括：
- 统一导出 `base` 与 `custom_components`
- 提供稳定的导入路径，降低重构影响
"""

from lfx.template.frontend_node import base, custom_components

__all__ = [
    "base",
    "custom_components",
]
