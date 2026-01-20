"""
模块名称：空操作数据库会话

本模块提供与 `AsyncSession` 兼容的空操作实现，用于禁用数据库场景。
主要功能包括：提供 `add/commit/exec` 等方法但不执行真实 I/O。

关键组件：`NoopSession`
设计背景：在 `use_noop_database` 场景下避免真实数据库依赖。
使用场景：单元测试或无数据库运行模式。
注意事项：所有方法均为无副作用，返回值仅用于兼容调用链。
"""


class NoopSession:
    """空操作会话实现，用于替代真实数据库会话。

    契约：
    - 输入：无。
    - 输出：提供与 `AsyncSession` 近似的调用接口。
    - 副作用：无真实数据库操作。
    - 失败语义：不抛异常，仅返回空值或占位对象。

    关键路径：调用任意方法均直接返回空结果。

    决策：以空实现满足依赖接口。
    问题：运行模式禁用数据库但调用链仍需会话对象。
    方案：提供与常用方法同名的空实现。
    代价：无法捕获真实数据库错误，问题可能被隐藏。
    重评：当需要更严格的测试保障时改为显式抛错。
    """

    class NoopBind:
        """空操作 `bind` 适配器，模拟 `Engine` 的 `connect` 行为。"""

        class NoopConnect:
            """空连接上下文，支持 `async with` 与 `run_sync`。"""

            async def __aenter__(self):
                """进入空连接上下文。

                契约：输出为 `self`，不触发任何 I/O。
                """
                return self

            async def __aexit__(self, exc_type, exc, tb):
                """退出空连接上下文。

                契约：无返回值，不处理异常。
                """
                pass

            async def run_sync(self, fn, *args, **kwargs):  # noqa: ARG002
                """同步执行占位函数。

                契约：忽略输入并返回 `None`，用于兼容 `run_sync` 调用链。
                """
                return None

        def connect(self):
            """返回空连接对象。

            契约：输出 `NoopConnect`，不建立真实连接。
            """
            return self.NoopConnect()

    bind = NoopBind()

    async def add(self, *args, **kwargs):
        """占位 `add`，不写入任何数据。"""
        pass

    async def commit(self):
        """占位提交操作，不触发事务提交。"""
        pass

    async def rollback(self):
        """占位回滚操作，不触发事务回滚。"""
        pass

    async def execute(self, *args, **kwargs):  # noqa: ARG002
        """占位执行，返回 `None` 以模拟无结果。"""
        return None

    async def query(self, *args, **kwargs):  # noqa: ARG002
        """占位查询，返回空列表以兼容调用方遍历。"""
        return []

    async def close(self):
        """占位关闭方法，无资源释放。"""
        pass

    async def refresh(self, *args, **kwargs):
        """占位刷新方法，不更新对象状态。"""
        pass

    async def delete(self, *args, **kwargs):
        """占位删除方法，不执行删除。"""
        pass

    async def __aenter__(self):
        """进入会话上下文，返回自身。"""
        return self

    async def __aexit__(self, exc_type, exc, tb):
        """退出会话上下文，不处理异常。"""
        pass

    async def get(self, *args, **kwargs):  # noqa: ARG002
        """占位获取方法，始终返回 `None`。"""
        return None

    async def exec(self, *args, **kwargs):  # noqa: ARG002
        """占位执行查询，返回空结果对象。"""

        class _NoopResult:
            def first(self):
                return None

            def all(self):
                return []

            def one_or_none(self):
                return None

        return _NoopResult()
