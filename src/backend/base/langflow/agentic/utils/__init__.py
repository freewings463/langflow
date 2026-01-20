"""
模块名称：`Agentic` 工具入口聚合

本模块提供 `Agentic` 工具函数的统一导出入口，便于上层按稳定路径引用。主要功能包括：
- 模板检索 `API` 的聚合导出

关键组件：
- `list_templates` / `get_template_by_id`：模板检索
- `get_all_tags` / `get_templates_count`：模板统计与标签枚举

设计背景：集中导出可减少调用方的导入路径耦合。
注意事项：仅做导出聚合，不包含业务逻辑。
"""

from langflow.agentic.utils.template_search import (
    get_all_tags,
    get_template_by_id,
    get_templates_count,
    list_templates,
)

__all__ = [
    "get_all_tags",
    "get_template_by_id",
    "get_templates_count",
    "list_templates",
]
