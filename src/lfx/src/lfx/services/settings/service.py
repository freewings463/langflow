"""
模块名称：settings.service

本模块提供设置服务的运行时封装，负责聚合核心设置与鉴权设置。
主要功能包括：
- 初始化 Settings 与 AuthSettings
- 提供统一的设置访问与更新入口

关键组件：
- SettingsService：设置服务实例

设计背景：配置对象分散在不同模块，需要在服务层汇聚以便依赖注入。
注意事项：初始化过程依赖 CONFIG_DIR，缺失会直接报错。
"""

from __future__ import annotations

from lfx.services.base import Service
from lfx.services.settings.auth import AuthSettings
from lfx.services.settings.base import Settings


class SettingsService(Service):
    """设置服务聚合器。

    契约：
    - 输入：Settings 与 AuthSettings
    - 输出：可读写的设置服务实例
    - 副作用：初始化时可能触发配置目录校验与密钥生成
    - 失败语义：配置不完整会抛出 ValueError
    """

    name = "settings_service"

    def __init__(self, settings: Settings, auth_settings: AuthSettings):
        super().__init__()
        self.settings: Settings = settings
        self.auth_settings: AuthSettings = auth_settings

    @classmethod
    def initialize(cls) -> SettingsService:
        """初始化设置服务。

        关键路径：
        1) 构建 Settings（触发环境变量与默认值解析）
        2) 校验 `CONFIG_DIR` 是否可用
        3) 构建 AuthSettings（可能生成/读取密钥）

        异常流：缺少 `CONFIG_DIR` 时抛出 ValueError。
        排障入口：查看启动日志中与 `CONFIG_DIR` 相关的错误信息。
        """
        settings = Settings()
        if not settings.config_dir:
            msg = "CONFIG_DIR must be set in settings"
            raise ValueError(msg)

        auth_settings = AuthSettings(
            CONFIG_DIR=settings.config_dir,
        )
        return cls(settings, auth_settings)

    def set(self, key, value):
        """按键更新设置项并返回当前 Settings。"""
        setattr(self.settings, key, value)
        return self.settings

    async def teardown(self):
        pass
