"""
模块名称：Telemetry 上报服务

本模块提供遥测事件的队列化发送与 OpenTelemetry 初始化。
主要功能：
- 组装与发送遥测 payload
- 队列化异步上报，支持批量拆分
- 捕获异常并上报
设计背景：集中遥测逻辑，避免业务层直接调用 HTTP。
注意事项：遵从 `do_not_track` 配置，关闭后不发送任何数据。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import platform
import traceback
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import httpx
from lfx.log.logger import logger

from langflow.services.base import Service
from langflow.services.telemetry.opentelemetry import OpenTelemetry
from langflow.services.telemetry.schema import (
    MAX_TELEMETRY_URL_SIZE,
    ComponentIndexPayload,
    ComponentInputsPayload,
    ComponentPayload,
    EmailPayload,
    ExceptionPayload,
    PlaygroundPayload,
    RunPayload,
    ShutdownPayload,
    VersionPayload,
)
from langflow.utils.version import get_version_info

if TYPE_CHECKING:
    from lfx.services.settings.service import SettingsService
    from pydantic import BaseModel


class TelemetryService(Service):
    """遥测上报服务。"""

    name = "telemetry_service"

    def __init__(self, settings_service: SettingsService):
        """初始化 Telemetry 服务与基础字段。

        契约：
        - 输入：`settings_service`
        - 副作用：创建 HTTP 客户端与 OTEL 实例
        """
        super().__init__()
        self.settings_service = settings_service
        self.base_url = settings_service.settings.telemetry_base_url
        self.telemetry_queue: asyncio.Queue = asyncio.Queue()
        self.client = httpx.AsyncClient(timeout=10.0)  # Set a reasonable timeout
        self.running = False
        self._stopping = False

        self.ot = OpenTelemetry(prometheus_enabled=settings_service.settings.prometheus_enabled)
        self.architecture: str | None = None
        self.worker_task: asyncio.Task | None = None
        # Check for do-not-track settings
        self.do_not_track = (
            os.getenv("DO_NOT_TRACK", "False").lower() == "true" or settings_service.settings.do_not_track
        )
        self.log_package_version_task: asyncio.Task | None = None
        self.log_package_email_task: asyncio.Task | None = None
        self.client_type = self._get_client_type()

        # Initialize static telemetry fields
        version_info = get_version_info()
        self.common_telemetry_fields = {
            "langflow_version": version_info["version"],
            "platform": "desktop" if self._get_langflow_desktop() else "python_package",
            "os": platform.system().lower(),
        }

    async def telemetry_worker(self) -> None:
        """遥测队列消费者，串行发送事件。"""
        while self.running:
            func, payload, path = await self.telemetry_queue.get()
            try:
                await func(payload, path)
            except Exception:  # noqa: BLE001
                await logger.aerror("Error sending telemetry data")
            finally:
                self.telemetry_queue.task_done()

    async def send_telemetry_data(self, payload: BaseModel, path: str | None = None) -> None:
        """发送单条遥测数据。

        契约：
        - 输入：`payload` 与可选路径
        - 失败语义：请求失败仅记录日志，不抛出异常
        """
        if self.do_not_track:
            await logger.adebug("Telemetry tracking is disabled.")
            return

        if payload.client_type is None:
            payload.client_type = self.client_type

        url = f"{self.base_url}"
        if path:
            url = f"{url}/{path}"

        try:
            payload_dict = payload.model_dump(by_alias=True, exclude_none=True, exclude_unset=True)

            # Add common fields to all payloads except VersionPayload
            if not isinstance(payload, VersionPayload):
                payload_dict.update(self.common_telemetry_fields)
            # Add timestamp dynamically
            if "timestamp" not in payload_dict:
                payload_dict["timestamp"] = datetime.now(timezone.utc).isoformat()

            response = await self.client.get(url, params=payload_dict)
            if response.status_code != httpx.codes.OK:
                await logger.aerror(f"Failed to send telemetry data: {response.status_code} {response.text}")
            else:
                await logger.adebug("Telemetry data sent successfully.")
        except httpx.HTTPStatusError as err:
            await logger.aerror(f"HTTP error occurred: {err}.")
        except httpx.RequestError as err:
            await logger.aerror(f"Request error occurred: {err}.")
        except Exception as err:  # noqa: BLE001
            await logger.aerror(f"Unexpected error occurred: {err}.")

    async def log_package_run(self, payload: RunPayload) -> None:
        """上报运行事件。"""
        await self._queue_event((self.send_telemetry_data, payload, "run"))

    async def log_package_shutdown(self) -> None:
        """上报关闭事件。"""
        payload = ShutdownPayload(time_running=(datetime.now(timezone.utc) - self._start_time).seconds)
        await self._queue_event(payload)

    async def _queue_event(self, payload) -> None:
        """将事件放入发送队列。"""
        if self.do_not_track or self._stopping:
            return
        await self.telemetry_queue.put(payload)

    def _get_langflow_desktop(self) -> bool:
        """判断是否为桌面版环境。"""
        # Coerce to bool, could be 1, 0, True, False, "1", "0", "True", "False"
        return str(os.getenv("LANGFLOW_DESKTOP", "False")).lower() in {"1", "true"}

    def _get_client_type(self) -> str:
        """返回客户端类型标识。"""
        return "desktop" if self._get_langflow_desktop() else "oss"

    async def _send_email_telemetry(self) -> None:
        """发送注册邮箱的遥测事件。"""
        from langflow.utils.registered_email_util import get_email_model

        payload: EmailPayload | None = get_email_model()

        if not payload:
            await logger.adebug("Aborted operation to send email telemetry event. No registered email address.")
            return

        await logger.adebug(f"Sending email telemetry event: {payload.email}")

        try:
            await self.log_package_email(payload=payload)
        except Exception as err:  # noqa: BLE001
            await logger.aerror(f"Failed to send email telemetry event: {payload.email}: {err}")
            return

        await logger.adebug(f"Successfully sent email telemetry event: {payload.email}")

    async def log_package_version(self) -> None:
        """上报版本与环境信息。"""
        python_version = ".".join(platform.python_version().split(".")[:2])
        version_info = get_version_info()
        if self.architecture is None:
            self.architecture = (await asyncio.to_thread(platform.architecture))[0]
        payload = VersionPayload(
            package=version_info["package"].lower(),
            version=version_info["version"],
            platform=platform.platform(),
            python=python_version,
            cache_type=self.settings_service.settings.cache_type,
            backend_only=self.settings_service.settings.backend_only,
            arch=self.architecture,
            auto_login=self.settings_service.auth_settings.AUTO_LOGIN,
            client_type=self.client_type,
        )
        await self._queue_event((self.send_telemetry_data, payload, None))

    async def log_package_email(self, payload: EmailPayload) -> None:
        """上报邮箱事件。"""
        await self._queue_event((self.send_telemetry_data, payload, "email"))

    async def log_package_playground(self, payload: PlaygroundPayload) -> None:
        """上报 Playground 事件。"""
        await self._queue_event((self.send_telemetry_data, payload, "playground"))

    async def log_package_component(self, payload: ComponentPayload) -> None:
        """上报组件执行事件。"""
        await self._queue_event((self.send_telemetry_data, payload, "component"))

    async def log_package_component_inputs(self, payload: ComponentInputsPayload) -> None:
        """上报组件输入值（必要时拆分）。"""
        # 实现：超出 URL 长度限制时拆分。
        chunks = payload.split_if_needed(max_url_size=MAX_TELEMETRY_URL_SIZE)

        # 实现：逐片入队发送。
        for chunk in chunks:
            await self._queue_event((self.send_telemetry_data, chunk, "component_inputs"))

    async def log_component_index(self, payload: ComponentIndexPayload) -> None:
        """上报组件索引加载事件。"""
        await self._queue_event((self.send_telemetry_data, payload, "component_index"))

    async def log_exception(self, exc: Exception, context: str) -> None:
        """上报未捕获异常。

        契约：
        - 输入：异常对象与上下文标识（`lifespan`/`handler`）
        - 失败语义：异常上报失败仅记录日志
        """
        # 实现：生成堆栈哈希用于聚合同类异常。
        stack_trace = traceback.format_exception(type(exc), exc, exc.__traceback__)
        stack_trace_str = "".join(stack_trace)
        #  Hash stack trace for grouping similar exceptions, truncated to save space
        stack_trace_hash = hashlib.sha256(stack_trace_str.encode()).hexdigest()[:16]

        payload = ExceptionPayload(
            exception_type=exc.__class__.__name__,
            exception_message=str(exc)[:500],  # Truncate long messages
            exception_context=context,
            stack_trace_hash=stack_trace_hash,
        )
        await self._queue_event((self.send_telemetry_data, payload, "exception"))

    def start(self) -> None:
        """启动遥测服务与后台任务。"""
        if self.running or self.do_not_track:
            return
        try:
            self.running = True
            self._start_time = datetime.now(timezone.utc)
            self.worker_task = asyncio.create_task(self.telemetry_worker())
            self.log_package_version_task = asyncio.create_task(self.log_package_version())
            if self._get_langflow_desktop():
                self.log_package_email_task = asyncio.create_task(self._send_email_telemetry())
        except Exception:  # noqa: BLE001
            logger.exception("Error starting telemetry service")

    async def flush(self) -> None:
        """等待队列消费完成。"""
        if self.do_not_track:
            return
        try:
            await self.telemetry_queue.join()
        except Exception:  # noqa: BLE001
            await logger.aexception("Error flushing logs")

    @staticmethod
    async def _cancel_task(task: asyncio.Task, cancel_msg: str) -> None:
        """取消异步任务并传播异常。"""
        task.cancel(cancel_msg)
        await asyncio.wait([task])
        if not task.cancelled():
            exc = task.exception()
            if exc is not None:
                raise exc

    async def stop(self) -> None:
        """停止遥测服务并清理资源。"""
        if self.do_not_track or self._stopping:
            return
        try:
            self._stopping = True
            # flush all the remaining events and then stop
            await self.flush()
            self.running = False
            if self.worker_task:
                await self._cancel_task(self.worker_task, "Cancel telemetry worker task")
            if self.log_package_version_task:
                await self._cancel_task(
                    self.log_package_version_task,
                    "Cancel telemetry log package version task",
                )
            if self.log_package_email_task:
                await self._cancel_task(
                    self.log_package_email_task,
                    "Cancel telemetry log package email task",
                )
            await self.client.aclose()
        except Exception:  # noqa: BLE001
            await logger.aexception("Error stopping tracing service")

    async def teardown(self) -> None:
        """服务销毁入口。"""
        await self.stop()
