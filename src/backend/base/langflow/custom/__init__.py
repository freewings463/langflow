"""
模块名称：`custom` 兼容入口

本模块聚合并转发 `lfx.custom` 的常用符号，为旧的 `langflow.custom` 导入路径提供兼容。主要功能包括：
- 统一导出 `Component` / `CustomComponent` 及常用工具函数
- 暴露 `utils` / `validate` 子模块，避免下游修改导入路径

关键组件：
- `build_custom_component_template`: 生成自定义组件模板
- `create_class` / `create_function`: 运行时构造可执行实体

设计背景：历史代码仍依赖 `langflow.custom`，迁移到 `lfx` 后需要稳定别名层。
注意事项：仅做符号转发，不引入运行时逻辑；更新导出时需同步 `__all__`。
"""

from lfx import custom as custom
from lfx.custom import custom_component as custom_component
from lfx.custom import utils as utils
from lfx.custom.custom_component.component import Component, get_component_toolkit
from lfx.custom.custom_component.custom_component import CustomComponent

from lfx.custom.utils import build_custom_component_template
from lfx.custom.validate import create_class, create_function, extract_class_name, extract_function_name

from . import validate

# 注意：显式导出用于稳定对外 `API`，新增/改名需同步此列表。
__all__ = [
    "Component",
    "CustomComponent",
    "build_custom_component_template",
    "create_class",
    "create_function",
    "custom",
    "custom_component",
    "extract_class_name",
    "extract_function_name",
    "get_component_toolkit",
    "utils",
    "validate",
]
