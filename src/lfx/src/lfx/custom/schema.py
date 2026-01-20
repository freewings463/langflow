"""
模块名称：自定义组件代码结构 Schema

本模块提供代码解析与结构化描述的 Pydantic 模型定义。
主要功能：
- 表达类/函数的结构信息；
- 提供缺省值占位类型。

设计背景：统一解析结果的结构，便于后续加工与展示。
注意事项：这些模型仅用于描述代码结构，不执行实际逻辑。
"""

from typing import Any

from pydantic import BaseModel, Field


class ClassCodeDetails(BaseModel):
    """类定义的结构化描述。"""

    name: str
    doc: str | None = None
    bases: list
    attributes: list
    methods: list
    init: dict | None = Field(default_factory=dict)


class CallableCodeDetails(BaseModel):
    """函数/可调用对象的结构化描述。"""

    name: str
    doc: str | None = None
    args: list
    body: list
    return_type: Any | None = None
    has_return: bool = False


class MissingDefault:
    """表示缺失默认值的占位类型。"""

    def __repr__(self) -> str:
        return "MISSING"
