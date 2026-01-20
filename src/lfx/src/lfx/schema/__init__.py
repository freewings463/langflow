"""Schema 模块入口。

本模块集中导出常用 schema 类型，并通过惰性导入避免循环依赖。
注意事项：访问未注册名称将抛出 `AttributeError`。
"""

__all__ = [
    "ComponentOutput",
    "Data",
    "DataFrame",
    "ErrorDetail",
    "InputValue",
    "JobStatus",
    "Message",
    "OpenAIErrorResponse",
    "OpenAIResponsesRequest",
    "OpenAIResponsesResponse",
    "OpenAIResponsesStreamChunk",
    "Tweaks",
    "UUIDstr",
    "WorkflowExecutionRequest",
    "WorkflowExecutionResponse",
    "WorkflowJobResponse",
    "WorkflowStatusResponse",
    "WorkflowStopRequest",
    "WorkflowStopResponse",
    "WorkflowStreamEvent",
    "dotdict",
]


def __getattr__(name: str):
    """按名称惰性导入 schema 类型。

    契约：输入为类型名字符串；输出对应类型。
    失败语义：未注册名称抛 `AttributeError`。
    """
    # 注意：避免循环导入
    if name == "Data":
        from .data import Data

        return Data
    if name == "DataFrame":
        from .dataframe import DataFrame

        return DataFrame
    if name == "dotdict":
        from .dotdict import dotdict

        return dotdict
    if name == "InputValue":
        from .graph import InputValue

        return InputValue
    if name == "Tweaks":
        from .graph import Tweaks

        return Tweaks
    if name == "Message":
        from .message import Message

        return Message
    if name == "UUIDstr":
        from .serialize import UUIDstr

        return UUIDstr
    if name == "OpenAIResponsesRequest":
        from .openai_responses_schemas import OpenAIResponsesRequest

        return OpenAIResponsesRequest
    if name == "OpenAIResponsesResponse":
        from .openai_responses_schemas import OpenAIResponsesResponse

        return OpenAIResponsesResponse
    if name == "OpenAIResponsesStreamChunk":
        from .openai_responses_schemas import OpenAIResponsesStreamChunk

        return OpenAIResponsesStreamChunk
    if name == "OpenAIErrorResponse":
        from .openai_responses_schemas import OpenAIErrorResponse

        return OpenAIErrorResponse
    if name == "WorkflowExecutionRequest":
        from .workflow import WorkflowExecutionRequest

        return WorkflowExecutionRequest
    if name == "WorkflowExecutionResponse":
        from .workflow import WorkflowExecutionResponse

        return WorkflowExecutionResponse
    if name == "WorkflowJobResponse":
        from .workflow import WorkflowJobResponse

        return WorkflowJobResponse
    if name == "WorkflowStreamEvent":
        from .workflow import WorkflowStreamEvent

        return WorkflowStreamEvent
    if name == "WorkflowStatusResponse":
        from .workflow import WorkflowStatusResponse

        return WorkflowStatusResponse
    if name == "WorkflowStopRequest":
        from .workflow import WorkflowStopRequest

        return WorkflowStopRequest
    if name == "WorkflowStopResponse":
        from .workflow import WorkflowStopResponse

        return WorkflowStopResponse
    if name == "JobStatus":
        from .workflow import JobStatus

        return JobStatus
    if name == "ErrorDetail":
        from .workflow import ErrorDetail

        return ErrorDetail
    if name == "ComponentOutput":
        from .workflow import ComponentOutput

        return ComponentOutput

    msg = f"module '{__name__}' has no attribute '{name}'"
    raise AttributeError(msg)
