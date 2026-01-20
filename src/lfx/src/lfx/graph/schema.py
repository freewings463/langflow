"""
模块名称：图执行结果与组件类型 Schema

本模块定义图运行结果的数据结构与组件类型枚举，供执行与前端展示使用。
主要功能包括：
- 规范化运行输出与消息格式
- 在校验阶段补齐输出数据
- 提供组件类型与输入/输出集合

关键组件：
- `ResultData`：单次执行结果载体
- `InterfaceComponentTypes`：组件类型枚举
- `RunOutputs`：执行输入/输出聚合

设计背景：统一图执行结果结构，减少上层解析分歧。
注意事项：`validate_model` 会基于 `artifacts` 回填 `outputs`。
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_serializer, model_validator

from lfx.schema.schema import OutputValue, StreamURL
from lfx.serialization.serialization import serialize
from lfx.utils.schemas import ChatOutputResponse, ContainsEnumMeta


class ResultData(BaseModel):
    """单次组件执行结果。

    契约：字段包含结果、日志、消息与耗时信息；`outputs` 可被自动补齐。
    失败语义：`validate_model` 仅做结构修补，不抛异常。
    """
    results: Any | None = Field(default_factory=dict)
    artifacts: Any | None = Field(default_factory=dict)
    outputs: dict | None = Field(default_factory=dict)
    logs: dict | None = Field(default_factory=dict)
    messages: list[ChatOutputResponse] | None = Field(default_factory=list)
    timedelta: float | None = None
    duration: str | None = None
    component_display_name: str | None = None
    component_id: str | None = None
    used_frozen_result: bool | None = False

    @field_serializer("results")
    def serialize_results(self, value):
        """序列化 `results` 字段，统一处理不可 JSON 化对象。"""
        if isinstance(value, dict):
            return {key: serialize(val) for key, val in value.items()}
        return serialize(value)

    @model_validator(mode="before")
    @classmethod
    def validate_model(cls, values):
        """基于 `artifacts` 补齐 `outputs` 字段。

        关键路径（三步）：
        1) 检查 `outputs` 是否为空
        2) 解析 `artifacts` 生成 `OutputValue`
        3) 写回 `outputs` 并返回
        """
        if not values.get("outputs") and values.get("artifacts"):
            # 实现：从 `artifacts` 构造 `outputs`。

            for key in values["artifacts"]:
                message = values["artifacts"][key]

                # 注意：临时兼容空值，避免序列化崩溃。
                if message is None:
                    continue

                if "stream_url" in message and "type" in message:
                    stream_url = StreamURL(location=message["stream_url"])
                    values["outputs"].update({key: OutputValue(message=stream_url, type=message["type"])})
                elif "type" in message:
                    values["outputs"].update({key: OutputValue(message=message, type=message["type"])})
        return values


class InterfaceComponentTypes(str, Enum, metaclass=ContainsEnumMeta):
    """前端交互组件类型枚举。"""
    ChatInput = "ChatInput"
    ChatOutput = "ChatOutput"
    TextInput = "TextInput"
    TextOutput = "TextOutput"
    DataOutput = "DataOutput"
    WebhookInput = "Webhook"


CHAT_COMPONENTS = [InterfaceComponentTypes.ChatInput, InterfaceComponentTypes.ChatOutput]
RECORDS_COMPONENTS = [InterfaceComponentTypes.DataOutput]
INPUT_COMPONENTS = [
    InterfaceComponentTypes.ChatInput,
    InterfaceComponentTypes.WebhookInput,
    InterfaceComponentTypes.TextInput,
]
OUTPUT_COMPONENTS = [
    InterfaceComponentTypes.ChatOutput,
    InterfaceComponentTypes.DataOutput,
    InterfaceComponentTypes.TextOutput,
]


class RunOutputs(BaseModel):
    """图执行的输入与输出聚合。"""
    inputs: dict = Field(default_factory=dict)
    outputs: list[ResultData | None] = Field(default_factory=list)
