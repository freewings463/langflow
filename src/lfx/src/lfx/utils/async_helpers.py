"""模块名称：异步运行与超时兼容工具

模块目的：为不同 Python 版本提供一致的 asyncio 超时与同步入口。
主要功能：
- 超时上下文：优先使用 `asyncio.timeout`，否则回退 `wait_for`
- 同步桥接：在已有事件循环时改用新线程执行协程
使用场景：同步调用链中运行协程并统一超时语义。
关键组件：`timeout_context`、`run_until_complete`
设计背景：旧版本缺少 `asyncio.timeout`，需要兼容处理。
注意事项：`run_until_complete` 会创建线程与事件循环，避免在热路径频繁调用。
"""

import asyncio
from contextlib import asynccontextmanager

if hasattr(asyncio, "timeout"):

    @asynccontextmanager
    async def timeout_context(timeout_seconds):
        """在支持 `asyncio.timeout` 的版本中提供统一的超时上下文。

        契约：超时抛出 `TimeoutError`，调用方需显式处理。
        """
        with asyncio.timeout(timeout_seconds) as ctx:
            yield ctx

else:

    @asynccontextmanager
    async def timeout_context(timeout_seconds):
        """在不支持 `asyncio.timeout` 的版本中模拟超时上下文。

        契约：超时抛出 `TimeoutError`，与新版本语义对齐。
        """
        try:
            # 注意：对永不完成的 Future 使用 `wait_for` 触发超时路径。
            yield await asyncio.wait_for(asyncio.Future(), timeout=timeout_seconds)
        except asyncio.TimeoutError as e:
            msg = f"Operation timed out after {timeout_seconds} seconds"
            raise TimeoutError(msg) from e


def run_until_complete(coro):
    """在同步代码中运行协程。

    契约：若当前线程已有运行中的事件循环，则新建线程与事件循环执行。
    副作用：创建线程与新事件循环；异常原样向上抛出。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # 注意：无事件循环时直接创建新循环执行协程。
        return asyncio.run(coro)
    # 注意：已有运行中的事件循环时，不能在同一线程阻塞执行，改用新线程+新事件循环。
    import concurrent.futures

    def run_in_new_loop():
        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)
        try:
            return new_loop.run_until_complete(coro)
        finally:
            new_loop.close()

    with concurrent.futures.ThreadPoolExecutor() as executor:
        future = executor.submit(run_in_new_loop)
        return future.result()
