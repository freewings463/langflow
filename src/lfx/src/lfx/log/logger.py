"""日志配置模块。

本模块基于 structlog 构建日志体系，支持缓冲读取与文件轮转。
主要功能包括：
- 动态配置日志级别与输出格式
- 缓冲日志以供 API 拉取
- 兼容 uvicorn/gunicorn 的日志拦截
"""

import json
import logging
import logging.handlers
import os
import sys
from collections import deque
from datetime import datetime
from pathlib import Path
from threading import Lock, Semaphore
from typing import Any, TypedDict

import orjson
import structlog
from platformdirs import user_cache_dir
from typing_extensions import NotRequired

from lfx.settings import DEV

VALID_LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

# 日志级别名称映射
LOG_LEVEL_MAP = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


class SizedLogBuffer:
    """A buffer for storing log messages for the log retrieval API."""

    def __init__(
        self,
        max_readers: int = 20,  # 最大并发读取数
    ):
        """Initialize the buffer.

        The buffer can be overwritten by an env variable LANGFLOW_LOG_RETRIEVER_BUFFER_SIZE
        because the logger is initialized before the settings_service are loaded.
        """
        self.buffer: deque = deque()

        self._max_readers = max_readers
        self._wlock = Lock()
        self._rsemaphore = Semaphore(max_readers)
        self._max = 0

    def get_write_lock(self) -> Lock:
        """获取写锁。"""
        return self._wlock

    def write(self, message: str) -> None:
        """写入一条日志到缓冲区。

        关键路径（三步）：
        1) 解析日志 JSON 并提取事件内容；
        2) 计算时间戳；
        3) 写入缓冲并裁剪超限内容。
        """
        record = json.loads(message)
        log_entry = record.get("event", record.get("msg", record.get("text", "")))

        # 注意：支持嵌套时间戳结构
        timestamp = record.get("timestamp", 0)
        if timestamp == 0 and "record" in record:
            time_info = record["record"].get("time", {})
            timestamp = time_info.get("timestamp", 0)

        if isinstance(timestamp, str):
            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            epoch = int(dt.timestamp() * 1000)
        else:
            epoch = int(timestamp * 1000)

        with self._wlock:
            if len(self.buffer) >= self.max:
                for _ in range(len(self.buffer) - self.max + 1):
                    self.buffer.popleft()
            self.buffer.append((epoch, log_entry))

    def __len__(self) -> int:
        """返回缓冲区长度。"""
        return len(self.buffer)

    def get_after_timestamp(self, timestamp: int, lines: int = 5) -> dict[int, str]:
        """获取指定时间戳之后的日志。"""
        rc = {}

        self._rsemaphore.acquire()
        try:
            with self._wlock:
                for ts, msg in self.buffer:
                    if lines == 0:
                        break
                    if ts >= timestamp and lines > 0:
                        rc[ts] = msg
                        lines -= 1
        finally:
            self._rsemaphore.release()

        return rc

    def get_before_timestamp(self, timestamp: int, lines: int = 5) -> dict[int, str]:
        """获取指定时间戳之前的日志。"""
        self._rsemaphore.acquire()
        try:
            with self._wlock:
                as_list = list(self.buffer)
            max_index = -1
            for i, (ts, _) in enumerate(as_list):
                if ts >= timestamp:
                    max_index = i
                    break
            if max_index == -1:
                return self.get_last_n(lines)
            rc = {}
            start_from = max(max_index - lines, 0)
            for i, (ts, msg) in enumerate(as_list):
                if start_from <= i < max_index:
                    rc[ts] = msg
            return rc
        finally:
            self._rsemaphore.release()

    def get_last_n(self, last_idx: int) -> dict[int, str]:
        """获取最后 N 条日志。"""
        self._rsemaphore.acquire()
        try:
            with self._wlock:
                as_list = list(self.buffer)
            return dict(as_list[-last_idx:])
        finally:
            self._rsemaphore.release()

    @property
    def max(self) -> int:
        """获取缓冲区最大大小。"""
        # 注意：从环境变量动态读取
        if self._max == 0:
            env_buffer_size = os.getenv("LANGFLOW_LOG_RETRIEVER_BUFFER_SIZE", "0")
            if env_buffer_size.isdigit():
                self._max = int(env_buffer_size)
        return self._max

    @max.setter
    def max(self, value: int) -> None:
        """设置缓冲区最大大小。"""
        self._max = value

    def enabled(self) -> bool:
        """判断缓冲区是否启用。"""
        return self.max > 0

    def max_size(self) -> int:
        """返回缓冲区最大大小。"""
        return self.max


# 用于日志拉取的缓冲区
log_buffer = SizedLogBuffer()


def add_serialized(_logger: Any, _method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """为日志条目添加序列化字段。"""
    # 注意：仅在启用缓冲时添加
    if log_buffer.enabled():
        subset = {
            "timestamp": event_dict.get("timestamp", 0),
            "message": event_dict.get("event", ""),
            "level": _method_name.upper(),
            "module": event_dict.get("module", ""),
        }
        event_dict["serialized"] = orjson.dumps(subset)
    return event_dict


def remove_exception_in_production(_logger: Any, _method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """在生产环境移除异常详情。"""
    if DEV is False:
        event_dict.pop("exception", None)
        event_dict.pop("exc_info", None)
    return event_dict


def buffer_writer(_logger: Any, _method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """将日志写入缓冲区（若启用）。"""
    if log_buffer.enabled() and "serialized" in event_dict:
        # 注意：复用序列化字段，避免重复序列化
        serialized_bytes = event_dict["serialized"]
        log_buffer.write(serialized_bytes.decode("utf-8"))
    return event_dict


class LogConfig(TypedDict):
    """Configuration for logging."""

    log_level: NotRequired[str]
    log_file: NotRequired[Path]
    disable: NotRequired[bool]
    log_env: NotRequired[str]
    log_format: NotRequired[str]


def configure(
    *,
    log_level: str | None = None,
    log_file: Path | None = None,
    disable: bool | None = False,
    log_env: str | None = None,
    log_format: str | None = None,
    log_rotation: str | None = None,
    cache: bool | None = None,
    output_file=None,
) -> None:
    """配置日志系统。

    关键路径（三步）：
    1) 解析环境变量与参数优先级；
    2) 组装 structlog 处理器与输出配置；
    3) 初始化 logger 并挂载文件/服务日志。
    """
    # 注意：若已配置且最小级别一致则直接返回
    cfg = structlog.get_config() if structlog.is_configured() else {}
    wrapper_class = cfg.get("wrapper_class")
    current_min_level = getattr(wrapper_class, "min_level", None)
    if os.getenv("LANGFLOW_LOG_LEVEL", "").upper() in VALID_LOG_LEVELS and log_level is None:
        log_level = os.getenv("LANGFLOW_LOG_LEVEL")

    log_level_str = os.getenv("LANGFLOW_LOG_LEVEL", "ERROR")
    if log_level is not None:
        log_level_str = log_level

    requested_min_level = LOG_LEVEL_MAP.get(log_level_str.upper(), logging.ERROR)
    if current_min_level == requested_min_level:
        return

    if log_level is None:
        log_level = "ERROR"

    if log_file is None:
        env_log_file = os.getenv("LANGFLOW_LOG_FILE", "")
        log_file = Path(env_log_file) if env_log_file else None

    if log_env is None:
        log_env = os.getenv("LANGFLOW_LOG_ENV", "")

    # 从环境变量读取日志格式（如未显式传入）
    if log_format is None:
        log_format = os.getenv("LANGFLOW_LOG_FORMAT")

    # 根据环境配置处理器链
    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    # 仅在 DEV 模式记录调用位置
    if DEV:
        processors.append(
            structlog.processors.CallsiteParameterAdder(
                parameters=[
                    structlog.processors.CallsiteParameter.FILENAME,
                    structlog.processors.CallsiteParameter.FUNC_NAME,
                    structlog.processors.CallsiteParameter.LINENO,
                ]
            )
        )

    processors.extend(
        [
            add_serialized,
            remove_exception_in_production,
            buffer_writer,
        ]
    )

    # 根据环境配置输出格式
    if log_env.lower() == "container" or log_env.lower() == "container_json":
        processors.append(structlog.processors.JSONRenderer())
    elif log_env.lower() == "container_csv":
        # DEV 模式追加调用位置信息
        key_order = ["timestamp", "level", "event"]
        if DEV:
            key_order += ["filename", "func_name", "lineno"]

        processors.append(structlog.processors.KeyValueRenderer(key_order=key_order, drop_missing=True))
    else:
        # 根据环境变量决定是否美化输出
        log_stdout_pretty = os.getenv("LANGFLOW_PRETTY_LOGS", "true").lower() == "true"
        if log_stdout_pretty:
            # 若指定自定义格式则使用 KeyValueRenderer
            if log_format:
                processors.append(structlog.processors.KeyValueRenderer())
            else:
                processors.append(structlog.dev.ConsoleRenderer(colors=True))
        else:
            processors.append(structlog.processors.JSONRenderer())

    # 解析日志级别
    numeric_level = LOG_LEVEL_MAP.get(log_level.upper(), logging.ERROR)

    # 创建 wrapper_class 并缓存 min_level
    wrapper_class = structlog.make_filtering_bound_logger(numeric_level)
    wrapper_class.min_level = numeric_level

    # 配置 structlog（默认 stdout）
    log_output_file = output_file if output_file is not None else sys.stdout

    structlog.configure(
        processors=processors,
        wrapper_class=wrapper_class,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=log_output_file)
        if not log_file
        else structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=cache if cache is not None else True,
    )

    # 如需写文件则设置文件处理器
    if log_file:
        if not log_file.parent.exists():
            cache_dir = Path(user_cache_dir("langflow"))
            log_file = cache_dir / "langflow.log"

        # 解析轮转配置
        if log_rotation:
            max_bytes = 10 * 1024 * 1024  # 默认 10MB
            if "MB" in log_rotation.upper():
                try:
                    parts = log_rotation.split()
                    expected_parts = 2
                    if len(parts) >= expected_parts and parts[1].upper() == "MB":
                        mb = int(parts[0])
                        if mb > 0:
                            max_bytes = mb * 1024 * 1024
                except (ValueError, IndexError):
                    pass
        else:
            max_bytes = 10 * 1024 * 1024  # 默认 10MB

        # 注意：structlog 无内建轮转，使用 stdlib 处理
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=5,
        )
        file_handler.setFormatter(logging.Formatter("%(message)s"))

        # 将文件处理器挂到 root logger
        logging.root.addHandler(file_handler)
        logging.root.setLevel(numeric_level)

    # 配置 uvicorn/gunicorn 日志重定向
    setup_uvicorn_logger()
    setup_gunicorn_logger()

    # 创建全局 logger
    global logger  # noqa: PLW0603
    logger = structlog.get_logger()

    if disable:
        # 通过设置极高日志级别来禁用输出
        structlog.configure(
            wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL),
        )

    logger.debug("Logger set up with log level: %s", log_level)


def setup_uvicorn_logger() -> None:
    """将 uvicorn 日志重定向到 structlog。"""
    loggers = (logging.getLogger(name) for name in logging.root.manager.loggerDict if name.startswith("uvicorn."))
    for uvicorn_logger in loggers:
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True


def setup_gunicorn_logger() -> None:
    """将 gunicorn 日志重定向到 structlog。"""
    logging.getLogger("gunicorn.error").handlers = []
    logging.getLogger("gunicorn.error").propagate = True
    logging.getLogger("gunicorn.access").handlers = []
    logging.getLogger("gunicorn.access").propagate = True


class InterceptHandler(logging.Handler):
    """Intercept standard logging messages and route them to structlog."""

    def emit(self, record: logging.LogRecord) -> None:
        """将标准日志转发到 structlog。"""
        # 获取对应 structlog logger
        logger_name = record.name
        structlog_logger = structlog.get_logger(logger_name)

        # 映射日志级别
        level = record.levelno
        if level >= logging.CRITICAL:
            structlog_logger.critical(record.getMessage())
        elif level >= logging.ERROR:
            structlog_logger.error(record.getMessage())
        elif level >= logging.WARNING:
            structlog_logger.warning(record.getMessage())
        elif level >= logging.INFO:
            structlog_logger.info(record.getMessage())
        else:
            structlog_logger.debug(record.getMessage())


# 初始化 logger（后续会在 configure 中重新配置）
logger: structlog.BoundLogger = structlog.get_logger()
configure(log_level="CRITICAL", cache=False)
