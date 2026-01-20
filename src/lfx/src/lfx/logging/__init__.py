"""模块名称：logging 兼容入口

本模块用于 `lfx.logging` 的向后兼容导出，实际实现已迁移至 `lfx.log`。
主要功能包括：重新导出 `configure/logger`，保持旧导入路径可用。

关键组件：
- `configure/logger`：来自 `lfx.log.logger` 的日志配置与实例

设计背景：避免历史代码因路径变更而中断。
注意事项：此模块仅做 re-export，不包含新逻辑。
"""

# 向后兼容：从新路径重新导出
from lfx.log.logger import configure, logger

# 保持原有 __all__ 导出
__all__ = ["configure", "logger"]
