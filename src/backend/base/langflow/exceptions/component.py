"""
模块名称：组件构建与流式处理异常

本模块定义组件生命周期内的专用异常，主要用于将错误上下文（来源、格式化回溯）显式传递给上层。
主要功能包括：
- `ComponentBuildError`：携带可展示的构建失败信息与格式化回溯。
- `StreamingError`：携带流式执行的根因异常与来源标识。

关键组件：`ComponentBuildError`、`StreamingError`。
设计背景：组件执行错误需要保留可观测上下文以便排障。
使用场景：组件构建失败、流式输出链路异常时。
注意事项：`formatted_traceback` 需为已格式化字符串，避免重复格式化。
"""

from langflow.schema.properties import Source


class ComponentBuildError(Exception):
    """组件构建失败的语义化异常。

    契约：输入 `message` 与 `formatted_traceback`，输出为异常实例；副作用：无。
    失败语义：异常即失败信号，调用方应记录 `formatted_traceback` 以便排障。
    """

    def __init__(self, message: str, formatted_traceback: str):
        """保存构建失败的上下文信息。

        契约：`message` 用于用户可读提示，`formatted_traceback` 用于日志/调试。
        副作用：无；失败语义：不额外抛出新异常。
        """
        self.message = message
        self.formatted_traceback = formatted_traceback
        super().__init__(message)


class StreamingError(Exception):
    """流式执行失败的语义化异常。

    契约：输入 `cause` 与 `source`，输出为异常实例；副作用：无。
    失败语义：异常链保留 `cause`，便于上层区分根因与来源组件。
    """

    def __init__(self, cause: Exception, source: Source):
        """保存流式失败的根因与来源。

        契约：`cause` 为原始异常，`source` 标识产生错误的组件。
        副作用：无；失败语义：`super().__init__(cause)` 保留根因文本。
        """
        self.cause = cause
        self.source = source
        super().__init__(cause)
