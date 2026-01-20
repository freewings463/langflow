"""
模块名称：聊天服务工厂

本模块提供 `ChatService` 的工厂封装，统一服务实例的创建入口。主要功能包括：
- 定义 `ChatServiceFactory` 以符合 `ServiceFactory` 约定

关键组件：
- `ChatServiceFactory.create`

设计背景：服务容器需要统一的实例化入口以便替换实现。
注意事项：当前 `create` 直接返回默认 `ChatService`，未注入额外配置。
"""

from langflow.services.chat.service import ChatService
from langflow.services.factory import ServiceFactory


class ChatServiceFactory(ServiceFactory):
    """`ChatService` 工厂封装。

    契约：继承 `ServiceFactory` 并绑定 `ChatService` 类型。
    副作用：无；失败语义：构造阶段不捕获异常，依赖父类初始化。
    关键路径（三步）：1) 绑定服务类型 2) 提供创建入口 3) 返回实例
    决策：通过工厂统一实例化
    问题：直接在调用方实例化不利于集中配置
    方案：提供 `create` 统一出口
    代价：增加一层抽象
    重评：当实例化逻辑稳定且无需集中配置时
    """

    def __init__(self) -> None:
        """初始化工厂并绑定服务类型。

        契约：无输入；输出为可用工厂实例。
        副作用：调用父类初始化；失败语义：父类异常向上传播。
        关键路径（三步）：1) 调用父类初始化 2) 绑定 `ChatService` 3) 返回
        决策：在初始化阶段绑定服务类型
        问题：避免 `create` 每次重复传入类型
        方案：将类型传给父类
        代价：降低运行期更换类型的灵活性
        重评：当需要动态切换服务实现时
        """
        super().__init__(ChatService)

    def create(self):
        """创建 `ChatService` 实例。

        契约：返回 `ChatService`；无输入参数。
        副作用：实例化对象；失败语义：构造异常向上传播。
        关键路径（三步）：1) 创建实例 2) 返回实例 3) 由调用方管理生命周期
        决策：默认直接实例化而不注入配置
        问题：当前无外部配置依赖
        方案：直接 `ChatService()` 返回
        代价：后续引入配置需修改工厂
        重评：当需要按环境注入配置或替换实现时
        """
        return ChatService()
