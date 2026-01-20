"""
模块名称：时间戳校验与转换

本模块提供时间戳字符串与 `datetime` 的双向转换与校验，主要用于事件模型字段标准化。主要功能包括：
- 解析多种时间戳格式并输出 `UTC` 字符串
- 将标准字符串转换为 `datetime`
- 生成带小数秒的时间字符串

关键组件：
- timestamp_to_str / str_to_timestamp
- timestamp_with_fractional_seconds

设计背景：外部输入格式不统一，需要统一为 `UTC` 字符串。
注意事项：解析失败会抛 `ValueError`，调用方需处理。
"""

from datetime import datetime, timezone

from pydantic import BeforeValidator


def timestamp_to_str(timestamp: datetime | str) -> str:
    """将时间戳转换为标准 `UTC` 字符串。

    契约：输出格式为 `YYYY-MM-DD HH:MM:SS UTC`。
    关键路径（三步）：
    1) 若为字符串，依次尝试支持格式并解析为 `UTC`。
    2) 若为 `datetime` 且无时区，补齐为 `UTC`。
    3) 格式化为标准字符串输出。
    失败语义：字符串格式无法解析时抛 `ValueError`。
    """
    if isinstance(timestamp, str):
        formats = [
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S %Z",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S%z",
        ]

        for fmt in formats:
            try:
                parsed = datetime.strptime(timestamp.strip(), fmt).replace(tzinfo=timezone.utc)
                return parsed.strftime("%Y-%m-%d %H:%M:%S %Z")
            except ValueError:
                continue

        msg = f"Invalid timestamp format: {timestamp}"
        raise ValueError(msg)

    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.strftime("%Y-%m-%d %H:%M:%S %Z")


def str_to_timestamp(timestamp: str | datetime) -> datetime:
    """将标准字符串转换为 `datetime`。

    契约：仅接受 `YYYY-MM-DD HH:MM:SS UTC` 字符串或 `datetime` 对象。
    失败语义：字符串格式不匹配时抛 `ValueError`。
    """
    if isinstance(timestamp, str):
        try:
            return datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S %Z").replace(tzinfo=timezone.utc)
        except ValueError as e:
            msg = f"Invalid timestamp format: {timestamp}. Expected format: YYYY-MM-DD HH:MM:SS UTC"
            raise ValueError(msg) from e
    return timestamp


def timestamp_with_fractional_seconds(timestamp: datetime | str) -> str:
    """将时间戳转换为带小数秒的 `UTC` 字符串。

    契约：输出格式为 `YYYY-MM-DD HH:MM:SS.ffffff UTC`。
    关键路径（三步）：
    1) 若为字符串，按支持格式尝试解析为 `UTC`。
    2) 若为 `datetime` 且无时区，补齐为 `UTC`。
    3) 格式化为带小数秒的字符串输出。
    失败语义：字符串格式无法解析时抛 `ValueError`。
    """
    if isinstance(timestamp, str):
        formats = [
            "%Y-%m-%d %H:%M:%S.%f %Z",
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S.%f%z",
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

    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.strftime("%Y-%m-%d %H:%M:%S.%f %Z")


timestamp_to_str_validator = BeforeValidator(timestamp_to_str)
timestamp_with_fractional_seconds_validator = BeforeValidator(timestamp_with_fractional_seconds)
str_to_timestamp_validator = BeforeValidator(str_to_timestamp)
