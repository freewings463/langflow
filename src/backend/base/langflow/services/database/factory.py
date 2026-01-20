"""
模块名称：数据库服务工厂

本模块提供 `DatabaseService` 的工厂化创建入口。
主要功能包括：校验数据库配置并生成 `DatabaseService` 实例。

关键组件：`DatabaseServiceFactory`
设计背景：统一服务实例的构建与配置校验，避免散落在调用方。
使用场景：服务注册或依赖注入时创建数据库服务。
注意事项：缺失 `database_url` 将抛出 `ValueError`。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langflow.services.database.service import DatabaseService
from langflow.services.factory import ServiceFactory

if TYPE_CHECKING:
    from lfx.services.settings.service import SettingsService


class DatabaseServiceFactory(ServiceFactory):
    """数据库服务工厂。

    契约：
    - 输入：初始化时无参数。
    - 输出：可通过 `create` 生成 `DatabaseService` 实例。
    - 失败语义：`create` 在缺失 `database_url` 时抛 `ValueError`。

    关键路径：
    1) 绑定 `DatabaseService` 类型。
    2) 校验配置并返回实例。

    决策：通过工厂统一创建数据库服务。
    问题：需要在服务注册处集中校验配置与创建逻辑。
    方案：继承 `ServiceFactory` 并覆盖 `create`。
    代价：配置校验集中后，调用方无法绕过检查。
    重评：当数据库服务支持多实例或多租户时扩展工厂参数。
    """

    def __init__(self) -> None:
        """初始化工厂并绑定服务类型。

        契约：无输入，输出为已绑定 `DatabaseService` 类型的工厂实例。
        关键路径：调用父类构造函数完成绑定。
        """
        super().__init__(DatabaseService)

    def create(self, settings_service: SettingsService):
        """创建数据库服务实例。

        契约：
        - 输入：`settings_service`，要求包含 `database_url`。
        - 输出：`DatabaseService` 实例。
        - 失败语义：当 `database_url` 为空时抛 `ValueError`。

        关键路径：
        1) 校验 `database_url` 是否存在。
        2) 构建并返回 `DatabaseService`。

        决策：在工厂层强制校验 `database_url`。
        问题：无数据库连接地址会导致服务无法运行。
        方案：缺失时直接抛错阻断启动。
        代价：配置错误无法延迟到运行期发现。
        重评：当支持无数据库模式或延迟连接时放宽限制。
        """
        if not settings_service.settings.database_url:
            msg = "No database URL provided"
            raise ValueError(msg)
        return DatabaseService(settings_service)
