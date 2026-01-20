"""
模块名称：settings.factory

本模块提供设置服务的工厂实现，统一创建流程并保证单例语义。
主要功能包括：
- 通过工厂模式创建 SettingsService
- 在进程内复用唯一实例

关键组件：
- SettingsServiceFactory：设置服务工厂

设计背景：服务创建流程需要集中控制，避免多实例导致配置不一致。
注意事项：该工厂返回的是设置服务的单例，若需多实例请勿复用此工厂。
"""

from typing_extensions import override

from lfx.services.factory import ServiceFactory
from lfx.services.settings.service import SettingsService


class SettingsServiceFactory(ServiceFactory):
    """设置服务工厂（进程内单例）。

    契约：
    - 输入：无显式输入
    - 输出：SettingsService 实例
    - 副作用：可能触发配置加载与磁盘读写
    - 失败语义：SettingsService.initialize 内部异常向上传递
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        super().__init__()
        self.service_class = SettingsService

    @override
    def create(self):
        """创建并初始化设置服务实例。"""
        return SettingsService.initialize()
