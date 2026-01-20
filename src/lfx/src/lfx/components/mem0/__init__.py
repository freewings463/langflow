"""模块名称：Mem0 组件出口

本模块提供 `mem0` 组件的统一导出入口，便于组件注册与发现。
使用场景：在组件加载或自动注册阶段引入 Mem0 记忆能力。
主要功能：暴露 `Mem0MemoryComponent` 供外部引用。

关键组件：
- Mem0MemoryComponent：Mem0 聊天记忆组件

设计背景：将组件导出集中到 `__init__` 以保持导入路径稳定
注意事项：仅做导出，不在此处引入任何副作用逻辑
"""

from .mem0_chat_memory import Mem0MemoryComponent

__all__ = ["Mem0MemoryComponent"]
