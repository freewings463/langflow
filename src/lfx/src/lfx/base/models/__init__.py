"""
模块名称：models 子包入口

本模块提供模型组件与统一模型查询接口的稳定导入路径，主要用于对外暴露
`LCModelComponent` 与统一模型查询函数。
主要功能包括：
- 聚合基础模型组件与统一模型查询 API
- 保持对外导入路径稳定，便于上层模块引用

关键组件：
- `LCModelComponent`：模型组件基类
- `get_model_providers` / `get_unified_models_detailed`：统一模型元数据入口

设计背景：对外输出统一入口，避免上层直接依赖内部实现细节。
注意事项：仅做导出聚合，不包含业务逻辑。
"""

from .model import LCModelComponent
from .unified_models import (
    get_model_provider_variable_mapping,
    get_model_providers,
    get_unified_models_detailed,
)

__all__ = [
    "LCModelComponent",
    "get_model_provider_variable_mapping",
    "get_model_providers",
    "get_unified_models_detailed",
]
