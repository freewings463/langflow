"""
模块名称：Tracing 类型转换工具

本模块提供将 Langflow 数据结构转换为 LangChain 兼容类型的工具。
主要功能包括：
- 递归转换 Message/Data/字典/列表

关键组件：
- `convert_to_langchain_type`
- `convert_to_langchain_types`

设计背景：不同 tracing SDK 需要 LangChain 类型输入。
注意事项：仅做类型转换，不做副作用操作。
"""

from typing import Any

from lfx.schema.data import Data


def convert_to_langchain_type(value):
    """将单个值递归转换为 LangChain 兼容类型。

    契约：支持 dict/list/Message/Data，其余返回原值。
    失败语义：不抛异常，依赖上游类型正确性。

    决策：Message 根据内容类型选择 prompt/message/document
    问题：Message 可能代表不同上下文
    方案：优先识别 `prompt`，其次 `sender`，否则文档
    代价：类型判断依赖 Message 内部结构
    重评：若 Message 结构变化需同步调整
    """
    from langflow.schema.message import Message

    if isinstance(value, dict):
        value = {key: convert_to_langchain_type(val) for key, val in value.items()}
    elif isinstance(value, list):
        value = [convert_to_langchain_type(v) for v in value]
    elif isinstance(value, Message):
        if "prompt" in value:
            value = value.load_lc_prompt()
        elif value.sender:
            value = value.to_lc_message()
        else:
            value = value.to_lc_document()
    elif isinstance(value, Data):
        value = value.to_lc_document() if "text" in value.data else value.data
    return value


def convert_to_langchain_types(io_dict: dict[str, Any]):
    """批量转换字典中的值为 LangChain 兼容类型。

    契约：返回新字典，不修改原始输入。
    失败语义：不抛异常。
    """
    converted = {}
    for key, value in io_dict.items():
        converted[key] = convert_to_langchain_type(value)
    return converted
