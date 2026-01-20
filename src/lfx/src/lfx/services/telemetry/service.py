"""
模块名称：lfx.services.telemetry.service

本模块提供 LFX 侧轻量遥测实现，主要用于记录事件但不发送外部数据。主要功能包括：
- 功能1：实现遥测接口并打印调试日志
- 功能2：在最小模式下避免网络发送

关键组件：
- `TelemetryService`：轻量遥测服务实现

设计背景：LFX 环境不默认开启外部遥测，仍保持与 Langflow 接口一致。
注意事项：`do_not_track=True`，所有事件仅记录日志。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lfx.log.logger import logger
from lfx.services.telemetry.base import BaseTelemetryService

if TYPE_CHECKING:
    from pydantic import BaseModel


class TelemetryService(BaseTelemetryService):
    """LFX 轻量遥测服务实现。

    契约：实现 `BaseTelemetryService` 全部接口，但不发送网络请求。
    关键路径：1) 设置 `do_not_track` 2) 记录事件日志 3) 维护服务状态。
    决策：默认不发送外部遥测；问题：LFX 运行环境无外发要求；
    方案：仅记录 `logger.debug`；代价：缺少外部聚合统计；
    重评：若需要远端分析再切换至完整实现。
    """

    def __init__(self):
        """初始化轻量遥测服务。

        契约：始终启用 `do_not_track` 并设置就绪状态。
        决策：默认禁用外发；问题：避免默认上传；
        方案：硬编码 `do_not_track=True`；代价：无法动态开启；
        重评：若引入配置开关再调整。
        """
        super().__init__()
        self.do_not_track = True
        self.set_ready()

    @property
    def name(self) -> str:
        """服务标识名称。

        契约：返回固定名称字符串。
        决策：名称硬编码；问题：服务注册需要稳定标识；
        方案：返回常量；代价：无法多实例区分；
        重评：如需多实例标识再改为可配置。
        """
        return "telemetry_service"

    async def send_telemetry_data(self, payload: BaseModel, path: str | None = None) -> None:  # noqa: ARG002
        """记录遥测事件（不发送）。

        契约：仅记录日志，不进行网络请求。
        决策：在最小实现中忽略 `payload`；问题：避免意外外发；
        方案：仅输出 `path`；代价：无法查看事件内容；
        重评：如需本地持久化可补充记录。
        """
        logger.debug(f"Telemetry event (not sent): {path}")

    async def log_package_run(self, payload: BaseModel) -> None:  # noqa: ARG002
        """记录运行事件（不发送）。 

        契约：仅输出调试日志。
        决策：不落地 payload；问题：轻量模式不做持久化；
        方案：记录固定关键词；代价：缺少上下文；
        重评：如需本地分析可补充 payload 序列化。
        """
        logger.debug("Telemetry: package run")

    async def log_package_shutdown(self) -> None:
        """记录关闭事件（不发送）。

        契约：输出调试日志即可。
        决策：关闭事件无 payload；问题：退出阶段上下文有限；
        方案：记录固定关键词；代价：信息不足；
        重评：如需记录关闭原因再扩展。
        """
        logger.debug("Telemetry: package shutdown")

    async def log_package_version(self) -> None:
        """记录版本事件（不发送）。

        契约：输出调试日志即可。
        决策：版本不从此处读取；问题：最小实现不依赖配置；
        方案：记录固定关键词；代价：缺少版本值；
        重评：如需版本值再读取配置并输出。
        """
        logger.debug("Telemetry: package version")

    async def log_package_playground(self, payload: BaseModel) -> None:  # noqa: ARG002
        """记录 Playground 交互（不发送）。

        契约：输出调试日志。
        决策：忽略 payload；问题：轻量模式避免外发；
        方案：记录固定关键词；代价：失去交互细节；
        重评：若本地分析需求增加再扩展。
        """
        logger.debug("Telemetry: playground interaction")

    async def log_package_component(self, payload: BaseModel) -> None:  # noqa: ARG002
        """记录组件使用（不发送）。

        契约：输出调试日志。
        决策：不记录组件明细；问题：避免额外存储；
        方案：固定日志文本；代价：无法统计组件使用；
        重评：如需统计再引入本地存储。
        """
        logger.debug("Telemetry: component usage")

    async def log_exception(self, exc: Exception, context: str) -> None:
        """记录未处理异常（不发送）。

        契约：记录异常类型与上下文字符串。
        决策：仅记录异常类名；问题：避免泄露详情；
        方案：使用 `exc.__class__.__name__`；代价：缺少堆栈；
        重评：如需详细排障再记录堆栈。
        """
        logger.debug(f"Telemetry: exception in {context}: {exc.__class__.__name__}")

    def start(self) -> None:
        """启动服务（最小实现）。

        契约：不做外部动作，仅记录日志。
        决策：启动为 noop；问题：最小实现无外部资源；
        方案：记录日志即可；代价：无法检测外部连接；
        重评：引入外部发送后再实现真实启动。
        """
        logger.debug("Telemetry service started (minimal mode)")

    async def stop(self) -> None:
        """停止服务（最小实现）。

        契约：不释放外部资源，仅记录日志。
        决策：停止为 noop；问题：无资源可释放；
        方案：记录日志；代价：无法验证关闭状态；
        重评：引入外部发送后实现清理。
        """
        logger.debug("Telemetry service stopped")

    async def flush(self) -> None:
        """刷新待发送数据（最小实现）。

        契约：不执行实际刷新。
        决策：flush 为 noop；问题：无发送队列；
        方案：空实现；代价：无法强制刷新；
        重评：引入缓冲队列后实现刷新。
        """

    async def teardown(self) -> None:
        """释放服务资源。

        契约：调用 `stop` 完成清理。
        决策：复用 `stop`；问题：避免重复逻辑；
        方案：直接 await `stop`；代价：无法区分 teardown/stop；
        重评：如需更细粒度清理再拆分。
        """
        await self.stop()
