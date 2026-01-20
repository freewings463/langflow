"""
模块名称：组件相关异常

本模块定义组件构建与流式处理过程中使用的异常类型。
主要功能包括：
- 组件构建失败的结构化错误承载
- 流式处理错误的来源标注

关键组件：
- `ComponentBuildError`：携带格式化回溯的构建异常
- `StreamingError`：携带异常来源的流式异常

设计背景：需要将错误消息与来源信息传递给上层处理。
注意事项：异常携带的字段会被上游用于日志或 UI 展示。
"""

from lfx.schema.properties import Source


class ComponentBuildError(Exception):
    """组件构建失败异常。

    契约：包含错误消息与格式化回溯，供上游展示/记录。
    失败语义：抛出即表示组件无法继续构建。
    """
    def __init__(self, message: str, formatted_traceback: str):
        self.message = message
        self.formatted_traceback = formatted_traceback
        super().__init__(message)


class StreamingError(Exception):
    """流式处理异常。

    契约：携带原始异常 `cause` 与来源 `source`。
    失败语义：用于标识流式输出中的失败位置。
    """
    def __init__(self, cause: Exception, source: Source):
        self.cause = cause
        self.source = source
        super().__init__(cause)
