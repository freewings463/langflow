"""
模块名称：lfx.services.telemetry

本模块提供遥测服务的对外入口，主要用于导出遥测服务实现。主要功能包括：
- 功能1：导出轻量遥测实现 `TelemetryService`

关键组件：
- `base`：遥测服务抽象接口
- `service`：轻量实现

设计背景：在 LFX 侧提供最小可用的遥测能力，保持与 Langflow 完整实现接口一致。
注意事项：默认实现不发送外部数据，仅记录本地日志。
"""

from .service import TelemetryService

__all__ = ["TelemetryService"]
