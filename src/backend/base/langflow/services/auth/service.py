"""
模块名称：认证服务定义

本模块提供认证服务的最小封装，用于承载认证相关的配置与依赖。
主要功能包括：
- 以 `Service` 基类形式暴露 `AuthService`。

关键组件：`AuthService`。
设计背景：服务层需要统一的认证服务实例以共享配置。
使用场景：认证工具函数访问 `settings_service`。
注意事项：当前仅保存依赖，不承担业务逻辑。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langflow.services.base import Service

if TYPE_CHECKING:
    from lfx.services.settings.service import SettingsService


class AuthService(Service):
    """认证服务容器。

    契约：构造时注入 `settings_service`；输出为可被依赖注入的服务实例。
    关键路径：初始化时保存 `settings_service`。
    副作用：无；失败语义：依赖注入失败则抛异常。
    决策：将认证配置挂载为服务实例
    问题：多处工具函数需要统一访问配置
    方案：在服务对象中保存 `settings_service`
    代价：服务对象仅承担依赖容器角色
    重评：若引入全局配置容器替代服务注入
    """

    name = "auth_service"

    def __init__(self, settings_service: SettingsService):
        """保存认证配置服务引用。

        契约：输入 `settings_service`，输出为实例初始化完成；副作用：无。
        关键路径：将依赖保存到 `self.settings_service`。
        失败语义：不做校验，依赖错误由上层捕获。
        决策：构造期不校验配置
        问题：服务创建阶段不应触发 I/O 或重逻辑
        方案：延迟到实际使用时再验证
        代价：配置错误可能在运行期才暴露
        重评：若启动时需要强制配置校验
        """
        self.settings_service = settings_service
