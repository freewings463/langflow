"""时间戳转换与校验器。"""

from datetime import datetime, timezone

from pydantic import BeforeValidator


def timestamp_to_str(timestamp: datetime | str) -> str:
    """将时间戳转换为标准字符串格式。"""
    if isinstance(timestamp, str):
        # 尝试多种格式解析
        formats = [
            "%Y-%m-%dT%H:%M:%S",  # ISO
            "%Y-%m-%d %H:%M:%S %Z",  # 含时区
            "%Y-%m-%d %H:%M:%S",  # 无时区
            "%Y-%m-%dT%H:%M:%S.%f",  # ISO+微秒
            "%Y-%m-%dT%H:%M:%S%z",  # ISO+数字时区
        ]

        for fmt in formats:
            try:
                parsed = datetime.strptime(timestamp.strip(), fmt).replace(tzinfo=timezone.utc)
                return parsed.strftime("%Y-%m-%d %H:%M:%S %Z")
            except ValueError:
                continue

        msg = f"Invalid timestamp format: {timestamp}"
        raise ValueError(msg)

    # 处理 datetime 对象
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.strftime("%Y-%m-%d %H:%M:%S %Z")


def str_to_timestamp(timestamp: str | datetime) -> datetime:
    """将字符串时间戳转换为 datetime（UTC）。"""
    if isinstance(timestamp, str):
        try:
            return datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S %Z").replace(tzinfo=timezone.utc)
        except ValueError as e:
            msg = f"Invalid timestamp format: {timestamp}. Expected format: YYYY-MM-DD HH:MM:SS UTC"
            raise ValueError(msg) from e
    return timestamp


def timestamp_with_fractional_seconds(timestamp: datetime | str) -> str:
    """将时间戳转换为含小数秒的字符串格式。"""
    if isinstance(timestamp, str):
        # 尝试多种格式解析
        formats = [
            "%Y-%m-%d %H:%M:%S.%f %Z",  # 含时区
            "%Y-%m-%d %H:%M:%S.%f",  # 无时区
            "%Y-%m-%dT%H:%M:%S.%f",  # ISO
            "%Y-%m-%dT%H:%M:%S.%f%z",  # ISO+数字时区
            # 兼容无小数秒
            "%Y-%m-%d %H:%M:%S %Z",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
        ]

        for fmt in formats:
            try:
                parsed = datetime.strptime(timestamp.strip(), fmt).replace(tzinfo=timezone.utc)
                return parsed.strftime("%Y-%m-%d %H:%M:%S.%f %Z")
            except ValueError:
                continue

        msg = f"Invalid timestamp format: {timestamp}"
        raise ValueError(msg)

    # 处理 datetime 对象
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.strftime("%Y-%m-%d %H:%M:%S.%f %Z")


timestamp_to_str_validator = BeforeValidator(timestamp_to_str)
timestamp_with_fractional_seconds_validator = BeforeValidator(timestamp_with_fractional_seconds)
str_to_timestamp_validator = BeforeValidator(str_to_timestamp)
