"""
模块名称：lfx.services.chat.schema

本模块提供聊天服务缓存协议定义，主要用于统一缓存读写的异步签名。主要功能包括：
- 功能1：定义读取缓存协议（`GetCache`）
- 功能2：定义写入缓存协议（`SetCache`）

关键组件：
- `GetCache`：异步读取协议
- `SetCache`：异步写入协议

设计背景：通过 `Protocol` 约束缓存实现的接口形态，便于依赖注入与替换。
注意事项：仅定义调用签名，具体异常与失败语义由实现者决定。
"""

import asyncio
from typing import Any, Protocol


class GetCache(Protocol):
    """缓存读取协议。

    契约：输入 `key` 与可选 `lock`，返回缓存数据或空值。
    异常流：由实现者决定；协议不约束错误类型。
    """

    async def __call__(self, key: str, lock: asyncio.Lock | None = None) -> Any: ...


class SetCache(Protocol):
    """缓存写入协议。

    契约：输入 `key` 与 `data`，返回是否写入成功的布尔值。
    异常流：由实现者决定；协议不约束错误类型。
    """

    async def __call__(self, key: str, data: Any, lock: asyncio.Lock | None = None) -> bool: ...
