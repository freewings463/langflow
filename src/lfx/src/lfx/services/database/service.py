"""
模块名称：数据库服务实现（Noop）

模块目的：提供无数据库依赖的占位数据库服务。
使用场景：离线运行或测试环境中替代真实数据库连接。
主要功能包括：
- 提供异步上下文的 Noop 会话

关键组件：
- `NoopDatabaseService`：无操作数据库服务

设计背景：确保 lfx 在不配置数据库时仍可运行。
注意：该服务不执行任何持久化操作，调用方需自行保障数据一致性。
"""

from __future__ import annotations

from contextlib import asynccontextmanager


class NoopDatabaseService:
    """无操作数据库服务。

    契约：所有会话操作返回 `NoopSession`，不触发真实持久化。
    关键路径：通过 `_with_session` 提供异步会话上下文。
    决策：以 Noop 实现满足接口约定。
    问题：离线/轻量场景需要可运行的数据库占位。
    方案：使用 `NoopSession` 代替真实连接。
    代价：所有写入操作都会被丢弃。
    重评：当需要离线持久化或缓存时。
    """

    @asynccontextmanager
    async def _with_session(self):
        """创建 Noop 会话的内部上下文。

        契约：返回异步上下文，产出 `NoopSession`。
        注意：不处理提交/回滚；仅用于底层封装。
        排障：若上层依赖真实事务，请替换为真实数据库服务。
        """
        from lfx.services.session import NoopSession

        async with NoopSession() as session:
            yield session
