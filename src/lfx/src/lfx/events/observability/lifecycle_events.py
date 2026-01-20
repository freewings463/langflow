"""生命周期可观测事件装饰器。

本模块提供 `observable` 装饰器，用于在异步方法前后发布生命周期事件。
主要功能包括：
- 在调用前/后/异常时生成事件负载
- 统一使用 `EventEncoder` 进行编码

注意事项：需要调用方提供 `event_manager`，否则仅记录告警。
"""

import functools
from collections.abc import Awaitable, Callable
from typing import Any

from ag_ui.encoder.encoder import EventEncoder

from lfx.log.logger import logger

AsyncMethod = Callable[..., Awaitable[Any]]

encoder: EventEncoder = EventEncoder()


def observable(observed_method: AsyncMethod) -> AsyncMethod:
    """将异步方法包装为可观测事件。

    约定：被装饰类可实现以下方法以生成事件负载：
    - `before_callback_event(*args, **kwargs)`
    - `after_callback_event(result, *args, **kwargs)`
    - `error_callback_event(exception, *args, **kwargs)`（可选）

    若方法未实现，将跳过事件发布而不报错。
    关键路径（三步）：
    1) 检查 `event_manager` 可用性；
    2) 执行前/后回调并编码负载；
    3) 捕获异常并触发错误回调。
    """

    async def check_event_manager(self, **kwargs):
        """校验事件管理器是否可用。"""
        if "event_manager" not in kwargs or kwargs["event_manager"] is None:
            await logger.awarning(
                f"EventManager not available/provided, skipping observable event publishing "
                f"from {self.__class__.__name__}"
            )
            return False
        return True

    async def before_callback(self, *args, **kwargs):
        """执行前置回调并编码事件负载。"""
        if not await check_event_manager(self, **kwargs):
            return

        if hasattr(self, "before_callback_event"):
            event_payload = self.before_callback_event(*args, **kwargs)
            event_payload = encoder.encode(event_payload)
            # TODO：按请求发布事件，需要基于上下文的队列
        else:
            await logger.awarning(
                f"before_callback_event not implemented for {self.__class__.__name__}. Skipping event publishing."
            )

    async def after_callback(self, res: Any | None = None, *args, **kwargs):
        """执行后置回调并编码事件负载。"""
        if not await check_event_manager(self, **kwargs):
            return
        if hasattr(self, "after_callback_event"):
            event_payload = self.after_callback_event(res, *args, **kwargs)
            event_payload = encoder.encode(event_payload)
            # TODO：按请求发布事件，需要基于上下文的队列
        else:
            await logger.awarning(
                f"after_callback_event not implemented for {self.__class__.__name__}. Skipping event publishing."
            )

    @functools.wraps(observed_method)
    async def wrapper(self, *args, **kwargs):
        """包装异步方法并发布生命周期事件。"""
        await before_callback(self, *args, **kwargs)
        result = None
        try:
            result = await observed_method(self, *args, **kwargs)
            await after_callback(self, result, *args, **kwargs)
        except Exception as e:
            await logger.aerror(f"Exception in {self.__class__.__name__}: {e}")
            if hasattr(self, "error_callback_event"):
                try:
                    event_payload = self.error_callback_event(e, *args, **kwargs)
                    event_payload = encoder.encode(event_payload)
                    # TODO：按请求发布事件，需要基于上下文的队列
                except Exception as callback_e:  # noqa: BLE001
                    await logger.aerror(
                        f"Exception during error_callback_event for {self.__class__.__name__}: {callback_e}"
                    )
            raise
        return result

    return wrapper
