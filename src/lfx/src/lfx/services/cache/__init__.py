"""
模块名称：Cache 服务导出入口

本模块统一导出缓存服务相关类型与常量，供上层模块引用。
主要功能：
- 对外导出 CacheService 抽象类；
- 暴露 CACHE_MISS/CacheMiss 标识。

设计背景：统一缓存接口与缺失标记的导入路径。
注意事项：新增缓存类型需同步更新 `__all__`。
"""

from .base import CacheService
from .utils import CACHE_MISS, CacheMiss

__all__ = ["CACHE_MISS", "CacheMiss", "CacheService"]
