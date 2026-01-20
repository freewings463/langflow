"""
模块名称：输入类型兼容导出

本模块集中重导出 `lfx` 的输入类型，主要用于保持旧导入路径可用。
主要功能包括：
- 暴露 `InputTypes`、`InputTypesMap` 与各类 `*Input` 类型。
- 转发 `instantiate_input`，便于从序列化数据实例化输入。

关键组件：`InputTypes`、`InputTypesMap`、`instantiate_input`。
设计背景：输入体系已迁移至 `lfx`，此处作为兼容层。
使用场景：历史代码从 `langflow.inputs.inputs` 引用输入类型。
注意事项：此处不新增实现逻辑，所有行为以 `lfx` 为准。
"""

from lfx.inputs.inputs import (
    AuthInput,
    BoolInput,
    CodeInput,
    DataFrameInput,
    DataInput,
    DefaultPromptField,
    DictInput,
    DropdownInput,
    FileInput,
    FloatInput,
    HandleInput,
    InputTypes,
    InputTypesMap,
    IntInput,
    LinkInput,
    McpInput,
    MessageInput,
    MessageTextInput,
    ModelInput,
    MultilineInput,
    MultilineSecretInput,
    MultiselectInput,
    NestedDictInput,
    PromptInput,
    QueryInput,
    SecretStrInput,
    SliderInput,
    StrInput,
    TabInput,
    TableInput,
    ToolsInput,
    instantiate_input,
)

__all__ = [
    "AuthInput",
    "BoolInput",
    "CodeInput",
    "DataFrameInput",
    "DataInput",
    "DefaultPromptField",
    "DictInput",
    "DropdownInput",
    "FileInput",
    "FloatInput",
    "HandleInput",
    "InputTypes",
    "InputTypesMap",
    "IntInput",
    "LinkInput",
    "McpInput",
    "MessageInput",
    "MessageTextInput",
    "ModelInput",
    "MultilineInput",
    "MultilineSecretInput",
    "MultiselectInput",
    "NestedDictInput",
    "PromptInput",
    "QueryInput",
    "SecretStrInput",
    "SliderInput",
    "StrInput",
    "TabInput",
    "TableInput",
    "ToolsInput",
    "instantiate_input",
]
