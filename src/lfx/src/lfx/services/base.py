"""
模块名称：服务基类

本模块定义服务的抽象基类与生命周期接口。
主要功能包括：
- 统一服务名称与就绪状态管理
- 定义服务销毁接口

设计背景：规范化服务生命周期与管理方式。
注意事项：具体服务需实现 `name` 与 `teardown`。
"""

from abc import ABC, abstractmethod


class Service(ABC):
    """服务基类。

    契约：提供 `ready` 状态与销毁接口。
    """

    def __init__(self):
        self._ready = False

    @property
    @abstractmethod
    def name(self) -> str:
        """服务名称。"""

    def set_ready(self) -> None:
        """将服务标记为就绪。"""
        self._ready = True

    @property
    def ready(self) -> bool:
        """返回服务是否就绪。"""
        return self._ready

    @abstractmethod
    async def teardown(self) -> None:
        """销毁服务并释放资源。"""
