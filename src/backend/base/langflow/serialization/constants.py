"""
模块名称：序列化常量兼容导出

本模块转发 `lfx.serialization.constants` 的长度限制常量，保持旧导入路径可用。
设计背景：序列化限制参数迁移到 `lfx` 后需要兼容层维持稳定 `API`。
注意事项：仅做符号转发，不添加运行时逻辑。
"""

from lfx.serialization.constants import MAX_ITEMS_LENGTH, MAX_TEXT_LENGTH

# 注意：显式导出用于稳定对外 `API`，新增常量需同步更新。
__all__ = ["MAX_ITEMS_LENGTH", "MAX_TEXT_LENGTH"]
