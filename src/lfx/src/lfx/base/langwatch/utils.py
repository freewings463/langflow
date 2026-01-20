"""
模块名称：langwatch.utils

本模块提供 LangWatch 评估器配置的获取与缓存工具。
主要功能包括：
- 功能1：通过 HTTP 拉取 evaluators 配置并进行缓存复用。

使用场景：运行时需要读取 LangWatch 的评估器列表以进行评估路由或提示。
关键组件：
- 函数 `get_cached_evaluators`

设计背景：减少重复网络请求与失败噪声，提高组件启动稳定性。
注意事项：缓存为进程级，变更需通过重启或清理缓存生效。
"""

from functools import lru_cache
from typing import Any

import httpx

from lfx.log.logger import logger


@lru_cache(maxsize=1)
def get_cached_evaluators(url: str) -> dict[str, Any]:
    """获取 LangWatch evaluators，并进行进程级缓存。

    契约：输入 `url` 为评估器接口地址；返回 `evaluators` 字典（缺失则为空）。
    关键路径：HTTP GET -> `raise_for_status` -> `response.json()` -> 取 `evaluators`。
    异常流：请求失败时记录日志并返回空字典。
    决策：
    问题：频繁拉取 evaluators 会带来网络波动与延迟。
    方案：使用 `lru_cache(maxsize=1)` 缓存首次成功结果。
    代价：缓存过期需重启或手动清理，可能读取到过期配置。
    重评：当 LangWatch 提供 ETag/缓存控制或需要热更新时。
    """
    # 注意：缓存命中时不会再次发起网络请求。
    try:
        response = httpx.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data.get("evaluators", {})
    except httpx.RequestError as e:
        # 排障：记录请求错误，避免静默失败导致评估器列表为空难以定位。
        logger.error(f"Error fetching evaluators: {e}")
        return {}
