"""
模块名称：日志启停控制

本模块提供日志启用/禁用的轻量级开关，避免重复设置导致状态混乱。
主要功能：
- 关闭 `langflow` 日志通道
- 启用 `langflow` 日志通道
设计背景：集中管理日志开关，便于测试或运行时静默。
注意事项：通过 `LOGGING_CONFIGURED` 防止重复切换。
"""

from lfx.log.logger import logger

LOGGING_CONFIGURED = False
"""日志开关是否已配置。"""


def disable_logging() -> None:
    """禁用 `langflow` 日志通道。

    契约：
    - 输入：无
    - 输出：无
    - 副作用：调用 `logger.disable("langflow")`
    """
    global LOGGING_CONFIGURED  # noqa: PLW0603
    if not LOGGING_CONFIGURED:
        logger.disable("langflow")
        LOGGING_CONFIGURED = True


def enable_logging() -> None:
    """启用 `langflow` 日志通道。

    契约：
    - 输入：无
    - 输出：无
    - 副作用：调用 `logger.enable("langflow")`
    """
    global LOGGING_CONFIGURED  # noqa: PLW0603
    logger.enable("langflow")
    LOGGING_CONFIGURED = True
