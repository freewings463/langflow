"""
模块名称：lfx.services.telemetry.base

本模块提供遥测服务抽象接口，主要用于统一不同实现的能力边界。主要功能包括：
- 功能1：定义发送与记录遥测事件的异步接口
- 功能2：约束服务生命周期方法（start/stop/flush）

关键组件：
- `BaseTelemetryService`：遥测服务抽象基类

设计背景：保持 LFX 与 Langflow 遥测实现一致接口，便于替换实现。
注意事项：仅定义接口，不包含具体发送逻辑。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from lfx.services.base import Service

if TYPE_CHECKING:
    from pydantic import BaseModel


class BaseTelemetryService(Service, ABC):
    """遥测服务抽象基类。

    契约：子类需实现发送遥测、事件记录与生命周期控制方法。
    关键路径：1) 初始化服务 2) 记录/发送事件 3) 生命周期管理。
    决策：使用抽象基类统一接口；问题：多实现易漂移；
    方案：强制实现接口并继承 `Service`；代价：实现方需适配异步签名；
    重评：若后续仅保留单一实现可简化接口层。
    """

    @abstractmethod
    def __init__(self):
        """初始化遥测服务。

        契约：子类需调用 `super().__init__()` 完成 `Service` 初始化。
        决策：要求显式初始化；问题：服务状态需要一致性；
        方案：通过基类约束调用顺序；代价：子类样板代码增加；
        重评：若迁移到依赖注入自动构建可简化。
        """
        super().__init__()

    @abstractmethod
    async def send_telemetry_data(self, payload: BaseModel, path: str | None = None) -> None:
        """发送遥测数据到后端。

        契约：`payload` 为序列化模型，`path` 可选用于拼接目标路径。
        决策：保留 `path` 参数；问题：不同事件需区分路由；
        方案：由调用方传入路径片段；代价：调用方需维护路由；
        重评：若统一单一路径可移除 `path`。
        """

    @abstractmethod
    async def log_package_run(self, payload: BaseModel) -> None:
        """记录包运行事件。

        契约：`payload` 必须包含运行元信息。
        决策：拆分独立事件方法；问题：不同事件字段不一致；
        方案：为运行事件提供专用入口；代价：接口数量增多；
        重评：若统一事件 schema 可合并为通用事件方法。
        """

    @abstractmethod
    async def log_package_shutdown(self) -> None:
        """记录包关闭事件。

        契约：无输入，记录一次关闭事件。
        决策：关闭事件不携带 payload；问题：关闭场景通常无结构化数据；
        方案：提供无参接口；代价：无法附加上下文；
        重评：如需附加上下文再引入 payload。
        """

    @abstractmethod
    async def log_package_version(self) -> None:
        """记录包版本信息。

        契约：无输入，记录当前版本快照。
        决策：版本单独记录；问题：版本信息在运行事件中可能缺失；
        方案：提供独立入口；代价：额外事件发送；
        重评：若运行事件覆盖版本可移除。
        """

    @abstractmethod
    async def log_package_playground(self, payload: BaseModel) -> None:
        """记录 Playground 交互事件。

        契约：`payload` 提供交互行为信息。
        决策：单独记录交互事件；问题：交互行为需独立分析；
        方案：拆分事件类型；代价：事件维度增加；
        重评：如无分析需求可合并。
        """

    @abstractmethod
    async def log_package_component(self, payload: BaseModel) -> None:
        """记录组件使用事件。

        契约：`payload` 提供组件标识与使用信息。
        决策：组件使用独立事件；问题：需要统计组件使用频次；
        方案：单独记录；代价：事件量增加；
        重评：若合并统计更可控可调整。
        """

    @abstractmethod
    async def log_exception(self, exc: Exception, context: str) -> None:
        """记录未处理异常。

        契约：`exc` 为异常实例，`context` 为上下文定位信息。
        决策：上下文用字符串；问题：结构化上下文成本高；
        方案：由调用方组装字符串；代价：一致性依赖调用方；
        重评：如需结构化日志再引入模型。
        """

    @abstractmethod
    def start(self) -> None:
        """启动遥测服务。

        契约：设置服务为可用状态；允许重复调用。
        决策：生命周期方法同步；问题：启动阶段需轻量；
        方案：同步启动，异步发送；代价：复杂初始化需另行处理；
        重评：如需异步启动再调整签名。
        """

    @abstractmethod
    async def stop(self) -> None:
        """停止遥测服务。

        契约：停止发送并释放资源。
        决策：停止方法异步；问题：可能存在待发送队列；
        方案：允许 await 完成清理；代价：调用方需 await；
        重评：若无清理需求可简化为同步。
        """

    @abstractmethod
    async def flush(self) -> None:
        """刷新待发送的遥测数据。

        契约：尽力发送缓冲数据；不保证成功。
        决策：提供显式 flush；问题：进程退出前需清空队列；
        方案：由调用方触发 flush；代价：增加调用复杂度；
        重评：若改用自动刷新可弱化该接口。
        """
