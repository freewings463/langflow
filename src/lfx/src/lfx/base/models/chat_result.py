"""
模块名称：聊天结果构建辅助

本模块提供从输入与系统消息构建 LangChain 消息序列，并调用模型得到结果的辅助函数。
主要功能包括：
- 将 `Message` 或纯文本转换为 LangChain 消息列表
- 组合系统消息与可选的 runnable 变换
- 统一调用模型的 invoke/stream 并处理异常信息

关键组件：
- `build_messages_and_runnable`
- `get_chat_result`

设计背景：在不同组件之间复用一致的消息构建与调用逻辑。
注意事项：对 `Message` 中的 `prompt` 走可组合 runnable 逻辑。
"""

import warnings

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from lfx.field_typing.constants import LanguageModel
from lfx.schema.message import Message


def build_messages_and_runnable(
    input_value: str | Message, system_message: str | None, original_runnable: LanguageModel
) -> tuple[list[BaseMessage], LanguageModel]:
    """构建消息列表并返回可执行 runnable。

    契约：返回 `(messages, runnable)`；当 `input_value` 为带 `prompt` 的 `Message` 时，
    runnable 会被组合进 prompt。
    副作用：无。
    失败语义：异常由调用方处理；内部仅忽略特定告警。
    """
    messages: list[BaseMessage] = []
    system_message_added = False
    runnable = original_runnable

    if input_value:
        if isinstance(input_value, Message):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                if "prompt" in input_value:
                    prompt = input_value.load_lc_prompt()
                    if system_message:
                        prompt.messages = [
                            SystemMessage(content=system_message),
                            *prompt.messages,  # type: ignore[has-type]
                        ]
                        system_message_added = True
                    runnable = prompt | runnable
                else:
                    messages.append(input_value.to_lc_message())
        else:
            messages.append(HumanMessage(content=input_value))

    if system_message and not system_message_added:
        messages.insert(0, SystemMessage(content=system_message))

    return messages, runnable


def get_chat_result(
    runnable: LanguageModel,
    input_value: str | Message,
    system_message: str | None = None,
    config: dict | None = None,
    *,
    stream: bool = False,
):
    """调用模型获取聊天结果或流式结果。

    契约：当 `stream=True` 时返回可迭代流；否则返回模型输出或内容。
    关键路径（三步）：
    1) 构建消息与 runnable（必要时组合 prompt）
    2) 根据 config 注入 output_parser 与运行配置
    3) 调用 stream 或 invoke 返回结果
    异常流：空输入抛 `ValueError`；模型调用异常按 config 转换或透传。
    性能瓶颈：由模型调用与网络延迟决定。
    排障入口：检查 `config` 中的 `_get_exception_message` 与回调配置。
    """
    if not input_value and not system_message:
        msg = "The message you want to send to the model is empty."
        raise ValueError(msg)

    messages, runnable = build_messages_and_runnable(
        input_value=input_value, system_message=system_message, original_runnable=runnable
    )

    inputs: list | dict = messages or {}
    try:
        if config and config.get("output_parser") is not None:
            runnable |= config["output_parser"]

        if config:
            runnable = runnable.with_config(
                {
                    "run_name": config.get("display_name", ""),
                    "project_name": config.get("get_project_name", lambda: "")(),
                    "callbacks": config.get("get_langchain_callbacks", list)(),
                }
            )
        if stream:
            return runnable.stream(inputs)
        message = runnable.invoke(inputs)
        return message.content if hasattr(message, "content") else message
    except Exception as e:
        if config and config.get("_get_exception_message") and (message := config["_get_exception_message"](e)):
            raise ValueError(message) from e
        raise
