"""
模块名称：自定义组件兼容导出

本模块转发 `lfx.custom.custom_component.custom_component` 的实现，主要用于旧路径兼容。主要功能包括：
- 保持历史导入路径可用
- 复用新版自定义组件实现

关键组件：
- CustomComponent（按 lfx 定义）

设计背景：历史导入路径迁移至 `lfx`，需要兼容旧引用。
注意事项：此处使用 `import *`，导出范围由上游模块控制。
"""

from lfx.custom.custom_component.custom_component import *  # noqa: F403
