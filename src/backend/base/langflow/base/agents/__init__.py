"""
模块名称：langflow.base.agents（兼容层）

本模块提供旧路径 `langflow.base.agents` 的兼容导出，主要用于
在迁移到 `lfx.base.agents` 后仍保持历史 import 可用。主要功能包括：
- 统一从新包路径导出公共符号

关键组件：
- `from lfx.base.agents import *`：一次性回导出

设计背景：历史代码与第三方扩展仍引用旧路径，需要平滑过渡
使用场景：老版本插件/脚本在不改代码的情况下继续运行
注意事项：仅做导出转发，不提供新实现；变更需同步到新包
"""

from lfx.base.agents import *  # noqa: F403
