"""
模块名称：API 异常与响应体封装

本模块提供将内部异常转为统一 `JSON` `detail` 的工具，主要用于 API 层错误返回。主要功能包括：
- 构建 `ExceptionBody`，统一 `message/traceback/description/code/suggestion`。
- 基于 `Flow` 计算过期组件建议并写入 `suggestion`。
- 以 `HTTPException` 形式返回序列化后的 `detail`。

关键组件：`ExceptionBody`、`APIException`、`InvalidChatInputError`。
设计背景：API 层需要稳定错误结构便于前端解析。
使用场景：接口失败或需提示过期组件时。
注意事项：`detail` 为 `JSON` 字符串，调用方需再反序列化。
"""

from fastapi import HTTPException
from pydantic import BaseModel

from langflow.api.utils import get_suggestion_message
from langflow.services.database.models.flow.model import Flow
from langflow.services.database.models.flow.utils import get_outdated_components


class InvalidChatInputError(Exception):
    """无效聊天输入的语义化异常。

    契约：由输入校验层抛出；调用方据此返回 4xx；无副作用。
    失败语义：异常即错误信号，需中断当前请求链路。
    """


class ExceptionBody(BaseModel):
    """API 错误响应体模型。

    契约：`message` 必填，其余字段可选；输出可被 `model_dump_json()` 序列化。
    副作用：无；失败语义：字段类型不匹配时由 `Pydantic` 抛 `ValidationError`。
    """

    message: str | list[str]
    traceback: str | list[str] | None = None
    description: str | list[str] | None = None
    code: str | None = None
    suggestion: str | list[str] | None = None


class APIException(HTTPException):
    """API 层异常包装器。

    契约：输入为 `exception`、可选 `flow`、`status_code`；输出为 `HTTPException`，
    `detail` 为 JSON 字符串。
    副作用：若提供 `flow`，会检查过期组件并生成 `suggestion`。
    失败语义：`exception` 始终通过 `str()` 序列化，避免不可序列化导致二次失败。
    """

    def __init__(self, exception: Exception, flow: Flow | None = None, status_code: int = 500):
        """构建错误响应体并写入 `detail`。

        契约：输入 `exception/flow/status_code`，输出为实例本身；副作用：无直接 I/O。
        失败语义：仅依赖 `build_exception_body`，不预期抛出新的异常。
        """
        body = self.build_exception_body(exception, flow)
        super().__init__(status_code=status_code, detail=body.model_dump_json())

    @staticmethod
    def build_exception_body(exc: str | list[str] | Exception, flow: Flow | None) -> ExceptionBody:
        """构造可序列化的错误响应体。

        契约：输入为 `exc` 与可选 `flow`，输出 `ExceptionBody`。
        副作用：若 `flow` 存在且包含过期组件，追加 `suggestion`。
        失败语义：无过期组件时不写 `suggestion`，`message` 固定写入 `str(exc)`。
        """
        body = {"message": str(exc)}
        if flow:
            outdated_components = get_outdated_components(flow)
            if outdated_components:
                body["suggestion"] = get_suggestion_message(outdated_components)
        return ExceptionBody(**body)
