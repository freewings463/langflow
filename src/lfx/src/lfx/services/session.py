"""
模块名称：轻量会话实现

本模块提供不依赖数据库的 Noop 会话实现，用于无数据库或测试场景。
主要功能包括：
- 提供与数据库会话兼容的接口
- 所有操作为 no-op，避免副作用

设计背景：在无数据库环境下保持接口可用。
注意事项：NoopSession 不持久化任何数据。
"""


class NoopSession:
    """空实现会话（no-op）。

    契约：提供完整会话接口但不执行任何操作。
    失败语义：不抛异常，仅返回空结果。
    """

    class NoopBind:
        class NoopConnect:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                pass

            async def run_sync(self, fn, *args, **kwargs):  # noqa: ARG002
                return None

        def connect(self):
            return self.NoopConnect()

    bind = NoopBind()

    async def add(self, *args, **kwargs):
        pass

    async def commit(self):
        pass

    async def rollback(self):
        pass

    async def execute(self, *args, **kwargs):  # noqa: ARG002
        return None

    async def query(self, *args, **kwargs):  # noqa: ARG002
        return []

    async def close(self):
        pass

    async def refresh(self, *args, **kwargs):
        pass

    async def delete(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    async def get(self, *args, **kwargs):  # noqa: ARG002
        return None

    async def exec(self, *args, **kwargs):  # noqa: ARG002
        class _NoopResult:
            def first(self):
                return None

            def all(self):
                return []

            def one_or_none(self):
                return None

        return _NoopResult()

    @property
    def no_autoflush(self):
        """禁用 autoflush 的上下文（空实现）。"""
        return self

    @property
    def is_active(self):
        """返回会话是否活跃（始终 True）。"""
        return True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        pass
