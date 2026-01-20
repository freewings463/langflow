"""
模块名称：自定义组件校验兼容导出

本模块转发 `lfx.custom.validate` 的校验/解析能力，主要用于旧路径兼容。主要功能包括：
- 提供类/函数解析与校验工具
- 保持历史导入路径可用

关键组件：
- validate 中的导出函数（按 lfx 定义）

设计背景：迁移到 `lfx` 后需兼容旧路径。
注意事项：此处使用 `import *`，导出范围由上游模块控制。
"""

from lfx.custom.validate import *  # noqa: F403
