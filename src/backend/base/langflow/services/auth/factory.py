"""
模块名称：认证服务工厂

本模块提供 `AuthService` 的工厂注册与实例化入口，主要用于服务容器统一创建认证服务。
主要功能包括：
- 以固定 `name` 标识注册认证服务工厂。
- 负责将 `settings_service` 注入 `AuthService`。

关键组件：`AuthServiceFactory`。
设计背景：服务层通过工厂模式解耦构造细节。
使用场景：应用启动或依赖注入阶段创建认证服务。
注意事项：工厂仅负责实例化，不做配置校验。
"""

from typing_extensions import override

from langflow.services.auth.service import AuthService
from langflow.services.factory import ServiceFactory


class AuthServiceFactory(ServiceFactory):
    """认证服务工厂。

    契约：`create` 接收 `settings_service`，返回 `AuthService` 实例；副作用：无。
    关键路径：`create` 实例化并返回 `AuthService`。
    失败语义：构造失败会向上抛异常，由服务容器处理。
    决策：使用工厂模式注册认证服务
    问题：需要统一的服务创建入口与生命周期管理
    方案：继承 `ServiceFactory` 并固定 `name`
    代价：增加一层间接调用
    重评：当服务容器不再使用工厂模式
    """

    name = "auth_service"

    def __init__(self) -> None:
        super().__init__(AuthService)

    @override
    def create(self, settings_service):
        """构造 `AuthService` 实例。

        契约：输入 `settings_service`，输出 `AuthService`；副作用：无。
        关键路径：调用 `AuthService(settings_service)`。
        失败语义：若依赖不完整，构造异常直接抛出。
        决策：直接透传 `settings_service`
        问题：认证服务只依赖配置，不需要额外组装逻辑
        方案：在工厂中直接实例化
        代价：无法在工厂层插入额外校验
        重评：若需要在构造前做配置预检查
        """
        return AuthService(settings_service)
