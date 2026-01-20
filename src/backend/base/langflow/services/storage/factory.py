"""模块名称：存储服务工厂

模块目的：根据配置创建对应的存储服务实例。
主要功能：依据 `storage_type` 选择本地或 `S3` 存储实现。
使用场景：服务启动时的依赖注入与实例化。
关键组件：`StorageServiceFactory`
设计背景：统一存储实现的构建入口，便于扩展。
注意事项：未知类型会回退本地存储并记录警告日志。
"""

from lfx.log.logger import logger
from lfx.services.settings.service import SettingsService
from typing_extensions import override

from langflow.services.factory import ServiceFactory
from langflow.services.session.service import SessionService
from langflow.services.storage.service import StorageService


class StorageServiceFactory(ServiceFactory):
    """存储服务工厂。"""

    def __init__(self) -> None:
        super().__init__(
            StorageService,
        )

    @override
    def create(self, session_service: SessionService, settings_service: SettingsService):
        """根据配置创建存储服务实例。

        契约：`storage_type` 取值为 `local` 或 `s3`；未知值回退本地。
        失败语义：构造服务失败时抛出原异常。
        """
        storage_type = settings_service.settings.storage_type
        if storage_type.lower() == "local":
            from .local import LocalStorageService

            return LocalStorageService(session_service, settings_service)
        if storage_type.lower() == "s3":
            from .s3 import S3StorageService

            return S3StorageService(session_service, settings_service)
        logger.warning(f"Storage type {storage_type} not supported. Using local storage.")
        from .local import LocalStorageService

        return LocalStorageService(session_service, settings_service)
