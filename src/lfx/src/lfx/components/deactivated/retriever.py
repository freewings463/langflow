"""
模块名称：Retriever Tool 组件（已停用）

本模块提供将 `BaseRetriever` 包装为 LangChain `Tool` 的能力，主要用于让 Agent 以工具形式调用检索器。主要功能包括：
- 使用 `create_retriever_tool` 生成工具

关键组件：
- `RetrieverToolComponent`：检索器工具组件

设计背景：旧流程中用于将检索器接入工具链。
注意事项：`name` 与 `description` 需清晰描述工具用途。
"""

from langchain_core.tools import create_retriever_tool

from lfx.custom.custom_component.custom_component import CustomComponent
from lfx.field_typing import BaseRetriever, Tool
from lfx.io import HandleInput, StrInput


class RetrieverToolComponent(CustomComponent):
    """检索器工具组件。

    契约：输入 `retriever`/`name`/`description`，输出 `Tool`。
    失败语义：检索器不兼容时由 `create_retriever_tool` 抛异常。
    副作用：无。
    """
    display_name = "RetrieverTool"
    description = "Tool for interacting with retriever"
    name = "RetrieverTool"
    icon = "LangChain"
    legacy = True

    inputs = [
        HandleInput(
            name="retriever",
            display_name="Retriever",
            info="Retriever to interact with",
            input_types=["Retriever"],
            required=True,
        ),
        StrInput(
            name="name",
            display_name="Name",
            info="Name of the tool",
            required=True,
        ),
        StrInput(
            name="description",
            display_name="Description",
            info="Description of the tool",
            required=True,
        ),
    ]

    def build(self, retriever: BaseRetriever, name: str, description: str, **kwargs) -> Tool:
        """构建检索器工具。

        契约：返回 `Tool`，可供 Agent 调用。
        失败语义：创建失败时抛异常。
        副作用：无。
        """
        _ = kwargs
        return create_retriever_tool(
            retriever=retriever,
            name=name,
            description=description,
        )
