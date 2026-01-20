"""UUID 序列化工具。"""

from typing import Annotated
from uuid import UUID

from pydantic import BeforeValidator


def str_to_uuid(v: str | UUID) -> UUID:
    """将字符串转换为 UUID。"""
    if isinstance(v, str):
        return UUID(v)
    return v


UUIDstr = Annotated[UUID, BeforeValidator(str_to_uuid)]
