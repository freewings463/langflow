"""
模块名称：服务工厂基类

本模块定义服务工厂的抽象基类与依赖约定。
主要功能包括：
- 统一服务创建入口
- 维护服务类与依赖列表

设计背景：通过工厂模式管理服务实例化。
注意事项：具体工厂需实现 `create`。
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lfx.services.base import Service


class ServiceFactory(ABC):
    """服务工厂基类。"""

    def __init__(self):
        """初始化工厂配置。"""
        self.service_class = None
        self.dependencies = []

    @abstractmethod
    def create(self, **kwargs) -> "Service":
        """创建服务实例。"""
