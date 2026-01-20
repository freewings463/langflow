"""
模块名称：存储服务出口

本模块对外暴露存储服务实现，供服务注册与依赖注入使用。
主要功能包括：
- 导出 `LocalStorageService`

关键组件：
- `LocalStorageService`

设计背景：集中管理存储服务入口，避免外部直接依赖实现路径。
注意事项：仅做导出，不包含业务逻辑。
"""

from lfx.services.storage.local import LocalStorageService

__all__ = ["LocalStorageService"]
