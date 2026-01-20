"""事件管理器。

本模块提供轻量事件注册与派发能力，支持将事件写入队列供流式消费。
主要功能包括：
- 注册事件并绑定回调
- 将事件序列化后推送到队列
- 提供默认事件管理器工厂

注意事项：回调签名必须包含 `manager`、`event_type`、`data` 三个参数。
"""

from __future__ import annotations

import inspect
import json
import time
import uuid
from functools import partial
from typing import TYPE_CHECKING

from fastapi.encoders import jsonable_encoder
from typing_extensions import Protocol

from lfx.log.logger import logger

if TYPE_CHECKING:
    # 仅用于类型提示的轻量定义
    LoggableType = dict | str | int | float | bool | list | None


class EventCallback(Protocol):
    def __call__(self, *, manager: EventManager, event_type: str, data: LoggableType): ...


class PartialEventCallback(Protocol):
    def __call__(self, *, data: LoggableType): ...


class EventManager:
    """事件管理器封装。

    契约：注册事件后可通过属性访问回调；`send_event` 输出到队列。
    副作用：写入队列并更新日志。
    失败语义：回调签名不合法抛 `ValueError`。
    """

    def __init__(self, queue):
        self.queue = queue
        self.events: dict[str, PartialEventCallback] = {}

    @staticmethod
    def _validate_callback(callback: EventCallback) -> None:
        """校验回调签名符合约定。"""
        if not callable(callback):
            msg = "Callback must be callable"
            raise TypeError(msg)
        # 注意：必须包含 manager/event_type/data 三个参数
        sig = inspect.signature(callback)
        parameters = ["manager", "event_type", "data"]
        if len(sig.parameters) != len(parameters):
            msg = "Callback must have exactly 3 parameters"
            raise ValueError(msg)
        if not all(param.name in parameters for param in sig.parameters.values()):
            msg = "Callback must have exactly 3 parameters: manager, event_type, and data"
            raise ValueError(msg)

    def register_event(
        self,
        name: str,
        event_type: str,
        callback: EventCallback | None = None,
    ) -> None:
        """注册事件并绑定回调。

        关键路径（三步）：
        1) 校验事件名格式；
        2) 构建回调并绑定事件类型；
        3) 写入事件映射表。
        """
        if not name:
            msg = "Event name cannot be empty"
            raise ValueError(msg)
        if not name.startswith("on_"):
            msg = "Event name must start with 'on_'"
            raise ValueError(msg)
        if callback is None:
            callback_ = partial(self.send_event, event_type=event_type)
        else:
            callback_ = partial(callback, manager=self, event_type=event_type)
        self.events[name] = callback_

    def send_event(self, *, event_type: str, data: LoggableType):
        """序列化事件并推送到队列。

        关键路径（三步）：
        1) 规范化数据并生成事件载荷；
        2) 序列化为 JSON 字符串；
        3) 写入队列（如可用）。
        """
        try:
            # 注意：避免引入重依赖，仅做轻量处理
            if isinstance(data, dict) and event_type in {"message", "error", "warning", "info", "token"}:
                # lfx 保持简化，不创建 playground 事件
                pass
        except Exception:  # noqa: BLE001
            logger.debug(f"Error processing event: {event_type}")
        jsonable_data = jsonable_encoder(data)
        json_data = {"event": event_type, "data": jsonable_data}
        event_id = f"{event_type}-{uuid.uuid4()}"
        str_data = json.dumps(json_data) + "\n\n"
        if self.queue:
            try:
                self.queue.put_nowait((event_id, str_data.encode("utf-8"), time.time()))
            except Exception:  # noqa: BLE001
                logger.debug("Queue not available for event")

    def noop(self, *, data: LoggableType) -> None:
        """空操作回调，用作缺省事件处理。"""
        pass

    def __getattr__(self, name: str) -> PartialEventCallback:
        """按名称获取事件回调，未注册则返回空操作。"""
        return self.events.get(name, self.noop)


def create_default_event_manager(queue=None):
    """创建包含默认事件的 EventManager。"""
    manager = EventManager(queue)
    manager.register_event("on_token", "token")
    manager.register_event("on_vertices_sorted", "vertices_sorted")
    manager.register_event("on_error", "error")
    manager.register_event("on_end", "end")
    manager.register_event("on_message", "add_message")
    manager.register_event("on_remove_message", "remove_message")
    manager.register_event("on_end_vertex", "end_vertex")
    manager.register_event("on_build_start", "build_start")
    manager.register_event("on_build_end", "build_end")
    return manager


def create_stream_tokens_event_manager(queue=None):
    """创建仅用于流式 token 的 EventManager。"""
    manager = EventManager(queue)
    manager.register_event("on_message", "add_message")
    manager.register_event("on_token", "token")
    manager.register_event("on_end", "end")
    return manager
