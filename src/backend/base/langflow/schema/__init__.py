"""
模块名称：`schema` 兼容导出聚合

本模块聚合 `lfx.schema` 常用类型并在 `langflow.schema` 下稳定导出，主要用于旧路径兼容。主要功能包括：
- 统一导出 `Data` / `DataFrame` / `Message` 等核心类型
- 暴露 `OpenAIResponses*` 相关请求与响应模型

关键组件：
- `Data` / `DataFrame` / `Message`
- OpenAIResponsesRequest / OpenAIResponsesResponse / OpenAIResponsesStreamChunk

设计背景：历史代码仍依赖 `langflow.schema` 路径，需要保持兼容。
注意事项：新增/调整导出需同步 `__all__`。
"""

from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame
from lfx.schema.dotdict import dotdict
from lfx.schema.message import Message
from lfx.schema.openai_responses_schemas import (
    OpenAIErrorResponse,
    OpenAIResponsesRequest,
    OpenAIResponsesResponse,
    OpenAIResponsesStreamChunk,
)
from lfx.schema.serialize import UUIDstr

__all__ = [
    "Data",
    "DataFrame",
    "Message",
    "OpenAIErrorResponse",
    "OpenAIResponsesRequest",
    "OpenAIResponsesResponse",
    "OpenAIResponsesStreamChunk",
    "UUIDstr",
    "dotdict",
]
