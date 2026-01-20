"""
模块名称：带缓存的组件兼容导出

本模块转发 `lfx.custom.custom_component.component_with_cache` 中的组件实现，主要用于旧路径兼容。主要功能包括：
- 保持历史导入路径可用
- 复用 lfx 侧缓存组件实现

关键组件：
- component_with_cache 中的导出符号（按 lfx 定义）

设计背景：自定义组件迁移到 `lfx` 后需兼容老路径。
注意事项：此处使用 `import *`，新增导出需在上游模块控制。
"""

from lfx.custom.custom_component.component_with_cache import *  # noqa: F403
