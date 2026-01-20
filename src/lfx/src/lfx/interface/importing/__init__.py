"""
模块名称：动态导入接口

本模块提供按名称动态导入类与模块的统一入口。主要功能包括：
- 暴露 `import_class` 与 `import_module`
- 作为 interface 层的导入适配器

关键组件：
- `import_class`：按类路径导入
- `import_module`：按模块路径导入

设计背景：统一动态导入逻辑，降低上层重复实现。
使用场景：运行时根据配置字符串加载实现类。
注意事项：导入失败会抛异常，调用方需处理。
"""

# 注意：集中导出工具函数，避免上层直接依赖实现细节。
from .utils import import_class, import_module

__all__ = ["import_class", "import_module"]
