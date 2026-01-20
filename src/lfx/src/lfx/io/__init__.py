"""
模块名称：lfx.io

本模块提供输入组件与输出组件的统一导出入口，主要用于组件实现中快速引用表单输入类型。主要功能包括：
- 功能1：集中导出 `lfx.inputs` 内常用输入类型
- 功能2：统一导出 `Output` 以保持调用路径稳定

关键组件：
- 输入类：`StrInput`/`IntInput`/`DropdownInput` 等
- 输出类：`Output`

设计背景：简化组件层依赖路径，避免散落的导入路径造成维护成本。
注意事项：仅聚合导出，不包含业务逻辑与运行时校验。
"""

from lfx.inputs import (
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
)
from lfx.template import Output

__all__ = [
    "BoolInput",
    "CodeInput",
    "DataFrameInput",
    "DataInput",
    "DefaultPromptField",
    "DefaultPromptField",
    "DictInput",
    "DropdownInput",
    "FileInput",
    "FloatInput",
    "HandleInput",
    "IntInput",
    "LinkInput",
    "LinkInput",
    "McpInput",
    "MessageInput",
    "MessageTextInput",
    "ModelInput",
    "MultilineInput",
    "MultilineSecretInput",
    "MultiselectInput",
    "NestedDictInput",
    "Output",
    "PromptInput",
    "QueryInput",
    "SecretStrInput",
    "SliderInput",
    "StrInput",
    "TabInput",
    "TableInput",
    "ToolsInput",
]
