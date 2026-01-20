"""
模块名称：代理错误类型

本模块定义代理相关的统一错误类型，主要用于将多提供商的请求错误映射为统一异常，
便于上层捕获与展示。
主要功能包括：
- 聚合多 `SDK` 的 `BadRequest` 错误
- 保留代理消息上下文用于排障

关键组件：
- `CustomBadRequestError`：统一的请求错误类型

设计背景：不同模型提供商的异常类型不一致，需要统一收敛。
注意事项：错误实例会携带 `agent_message`，上层可用于回滚或清理。
"""

from anthropic import BadRequestError as AnthropicBadRequestError
from cohere import BadRequestError as CohereBadRequestError
from httpx import HTTPStatusError

from lfx.schema.message import Message


class CustomBadRequestError(AnthropicBadRequestError, CohereBadRequestError, HTTPStatusError):
    """自定义错误类，用于处理代理相关的错误

    契约：
    - 输入：代理消息和错误消息
    - 输出：CustomBadRequestError 实例
    - 副作用：继承多个错误类型的特性
    - 失败语义：表示特定的错误情况
    """
    def __init__(self, agent_message: Message | None, message: str):
        """初始化自定义错误

        契约：
        - 输入：可选的代理消息和错误消息
        - 输出：CustomBadRequestError 实例
        - 副作用：设置错误消息和代理消息属性
        - 失败语义：无
        """
        super().__init__(message)
        self.message = message
        self.agent_message = agent_message

    def __str__(self):
        """返回错误的字符串表示

        契约：
        - 输入：无
        - 输出：错误消息字符串
        - 副作用：无
        - 失败语义：无
        """
        return f"{self.message}"
